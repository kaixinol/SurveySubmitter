from __future__ import annotations

import copy
import random
from loguru import logger
from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict

if TYPE_CHECKING:
    from survey_submitter.core.questions.config import QuestionEntry

from survey_submitter.core.reverse_fill import (
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SCORE,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_FORMAT_WJX_TEXT,
)
from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.types import QuestionType
from survey_submitter.core.config.schema import (
    AnswerConfigSection,
    ExecutionSection,
    QuestionInfo,
    RuntimeConfig,
    SurveySection,
)
from survey_submitter.core.questions.consistency import normalize_rule_dict, sanitize_answer_rules
from survey_submitter.core.questions.utils import serialize_random_int_range
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    normalize_survey_provider,
)
from survey_submitter.providers.contracts import SurveyQuestionMeta
from survey_submitter.constants import USER_AGENT_PRESETS

_TEXT_RANDOM_MODES = {"none", "name", "mobile", "id_card", "integer"}
_REVERSE_FILL_FORMATS = {
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_FORMAT_WJX_SCORE,
    REVERSE_FILL_FORMAT_WJX_TEXT,
}
DEFAULT_ANSWER_DURATION_RANGE_SECONDS = (60, 120)
MAX_ANSWER_DURATION_SECONDS = 30 * 60
_DEFAULT_RANDOM_UA_RATIOS = {"wechat": 33, "mobile": 33, "pc": 34}
_USER_AGENT_DEVICE_TO_PRESET_KEYS = {
    "wechat": ["wechat_android"],
    "mobile": ["mobile_android"],
    "pc": ["pc_web"],
}


class UserAgentProfile(BaseConfigModel):
    model_config = ConfigDict(frozen=True)
    category: str
    preset_key: str
    ua: str
    label: str


__all__ = [
    "CURRENT_CONFIG_SCHEMA_VERSION",
    "UserAgentProfile",
    "_select_user_agent_from_ratios",
    "serialize_question_entry",
    "deserialize_question_entry",
    "clone_question_entries",
    "survey_questions_from_definition",
    "build_runtime_config_snapshot",
    "normalize_runtime_config_payload",
    "serialize_runtime_config",
    "deserialize_runtime_config",
    "_ensure_supported_config_payload",
]

_CONFIG_CORRUPTED_MESSAGE = "该配置文件损坏，请输入问卷链接/二维码重新配置"
CURRENT_CONFIG_SCHEMA_VERSION = 6

_QUESTION_ENTRY_FIELDS = {
    "question_type",
    "probabilities",
    "texts",
    "rows",
    "option_count",
    "distribution_mode",
    "custom_weights",
    "question_num",
    "question_title",
    "survey_provider",
    "provider_question_id",
    "provider_page_id",
    "ai_enabled",
    "multi_text_blank_modes",
    "multi_text_blank_ai_flags",
    "multi_text_blank_int_ranges",
    "text_random_mode",
    "text_random_int_range",
    "option_fill_texts",
    "fillable_option_indices",
    "attached_option_selects",
    "is_location",
    "location_parts",
    "dimension",
}

_SURVEY_SECTION_FIELDS = frozenset(SurveySection.model_fields.keys())
_EXECUTION_SECTION_FIELDS = frozenset(ExecutionSection.model_fields.keys())
_ANSWER_CONFIG_SECTION_FIELDS = frozenset(AnswerConfigSection.model_fields.keys())
_SECTION_KEYS = frozenset({"survey", "execution", "answer_config"})


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, (int, float, str)):
            return int(value)
        return default
    except (ValueError, TypeError):
        return default


def _normalize_question_type(value: object) -> str:
    raw = str(value or "").strip() or QuestionType.UNKNOWN
    try:
        return str(QuestionType(raw))
    except ValueError:
        return str(QuestionType.UNKNOWN)


def _as_str(value: object, default: str = "") -> str:
    text = str(value or default).strip()
    return text or default


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        return default
    return bool(value)


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    return []


def _select_user_agent_from_ratios(
    ratios: dict[str, int],
    *,
    rng: random.Random | None = None,
) -> UserAgentProfile | None:
    chooser = rng or random
    devices: list[str] = []
    weights: list[int] = []
    for device_type, ua_keys in _USER_AGENT_DEVICE_TO_PRESET_KEYS.items():
        if not ua_keys:
            continue
        weight = max(0, _coerce_int((ratios or {}).get(device_type), 0))
        if weight > 0:
            devices.append(device_type)
            weights.append(weight)
    if not devices:
        return None

    device_type = chooser.choices(devices, weights=weights, k=1)[0]
    ua_keys = _USER_AGENT_DEVICE_TO_PRESET_KEYS.get(device_type, [])
    if not ua_keys:
        return None

    key = chooser.choice(ua_keys)
    preset = USER_AGENT_PRESETS.get(key) or {}
    ua = str(preset.get("ua") or "").strip()
    label = str(preset.get("label") or "").strip()
    if not ua:
        return None
    return UserAgentProfile(
        category=str(device_type or "").strip(),
        preset_key=str(key or "").strip(),
        ua=ua,
        label=label,
    )


def _prob_config_is_unset(value: object) -> bool:
    if value is None:
        return True
    if value == -1:
        return True
    if isinstance(value, (list, tuple)):
        if not value:
            return True
        for item in value:
            if isinstance(item, (int, float, str)):
                try:
                    if float(item) > 0:
                        return False
                except (ValueError, TypeError):
                    continue
        return True
    return False


def _custom_weights_has_positive(weights: object) -> bool:
    if not isinstance(weights, list) or not weights:
        return False
    stack: list[object] = list(weights)
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
            continue
        try:
            if isinstance(item, (int, float, str)) and float(item) > 0:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _normalize_multi_text_blank_modes(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    for item in raw:
        mode = str(item or "none").strip().lower()
        normalized.append(mode if mode in _TEXT_RANDOM_MODES else "none")
    return normalized


def _normalize_multi_text_blank_ai_flags(raw: object) -> list[bool]:
    if not isinstance(raw, list):
        return []
    return [bool(item) for item in raw]


def _normalize_random_int_range(raw: object) -> list[int]:
    if raw in (None, "", []):
        return []
    return serialize_random_int_range(raw)


def _normalize_multi_text_blank_int_ranges(raw: object) -> list[list[int]]:
    if not isinstance(raw, list):
        return []
    return [_normalize_random_int_range(item) for item in raw]


def _legacy_answer_duration_to_range(value: int) -> tuple[int, int]:
    normalized = min(MAX_ANSWER_DURATION_SECONDS, max(0, int(value or 0)))
    if normalized <= 0:
        return DEFAULT_ANSWER_DURATION_RANGE_SECONDS
    low = max(0, int(round(normalized * 0.9)))
    high = min(MAX_ANSWER_DURATION_SECONDS, max(low, int(round(normalized * 1.1))))
    return low, high


def _normalize_dimension_value(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text or text == "未分组":
        return None
    return text


def serialize_question_entry(entry) -> dict[str, object]:
    probabilities = entry.probabilities
    if (
        entry.distribution_mode == "custom"
        and _prob_config_is_unset(probabilities)
        and _custom_weights_has_positive(entry.custom_weights)
    ):
        probabilities = entry.custom_weights
    return {
        "question_type": entry.question_type,
        "probabilities": probabilities,
        "texts": entry.texts,
        "rows": entry.rows,
        "option_count": entry.option_count,
        "distribution_mode": entry.distribution_mode,
        "custom_weights": entry.custom_weights,
        "question_num": entry.question_num,
        "question_title": entry.question_title,
        "survey_provider": normalize_survey_provider(entry.survey_provider),
        "provider_question_id": str(entry.provider_question_id or ""),
        "provider_page_id": str(entry.provider_page_id or ""),
        "ai_enabled": bool(entry.ai_enabled),
        "multi_text_blank_modes": _normalize_multi_text_blank_modes(entry.multi_text_blank_modes),
        "multi_text_blank_ai_flags": _normalize_multi_text_blank_ai_flags(
            entry.multi_text_blank_ai_flags
        ),
        "multi_text_blank_int_ranges": _normalize_multi_text_blank_int_ranges(
            entry.multi_text_blank_int_ranges
        ),
        "text_random_mode": str(entry.text_random_mode or "none"),
        "text_random_int_range": _normalize_random_int_range(entry.text_random_int_range),
        "option_fill_texts": entry.option_fill_texts,
        "fillable_option_indices": entry.fillable_option_indices,
        "attached_option_selects": list(entry.attached_option_selects or []),
        "is_location": entry.is_location,
        "location_parts": list(entry.location_parts or []),
        "dimension": _normalize_dimension_value(entry.dimension),
    }


def deserialize_question_entry(data: dict[str, object]) -> QuestionEntry:
    from survey_submitter.core.questions.config import QuestionEntry

    if not isinstance(data, dict):
        raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：题目配置必须是字典类型")

    normalized_data = dict(data)

    mode_raw = str(normalized_data.get("distribution_mode") or "random").strip()
    probabilities = normalized_data.get("probabilities")
    custom_weights = normalized_data.get("custom_weights")

    if (
        mode_raw == "custom"
        and _prob_config_is_unset(probabilities)
        and _custom_weights_has_positive(custom_weights)
    ):
        probabilities = custom_weights
    if (
        mode_raw == "custom"
        and (custom_weights is None or custom_weights == [])
        and isinstance(probabilities, list)
    ):
        custom_weights = list(probabilities)

    normalized_data.update({
        "distribution_mode": mode_raw,
        "probabilities": probabilities,
        "custom_weights": custom_weights,
        "question_type": _normalize_question_type(normalized_data.get("question_type")),
        "rows": _coerce_int(normalized_data.get("rows"), 1),
        "option_count": _coerce_int(normalized_data.get("option_count"), 0),
        "survey_provider": normalize_survey_provider(
            normalized_data.get("survey_provider"),
            default=SURVEY_PROVIDER_WJX,
        ),
        "provider_question_id": str(normalized_data.get("provider_question_id") or "").strip() or None,
        "provider_page_id": str(normalized_data.get("provider_page_id") or "").strip() or None,
        "ai_enabled": bool(normalized_data.get("ai_enabled", False)),
        "multi_text_blank_modes": _normalize_multi_text_blank_modes(
            normalized_data.get("multi_text_blank_modes")
        ),
        "multi_text_blank_ai_flags": _normalize_multi_text_blank_ai_flags(
            normalized_data.get("multi_text_blank_ai_flags")
        ),
        "multi_text_blank_int_ranges": _normalize_multi_text_blank_int_ranges(
            normalized_data.get("multi_text_blank_int_ranges")
        ),
        "text_random_mode": str(normalized_data.get("text_random_mode") or "none").strip(),
        "text_random_int_range": _normalize_random_int_range(
            normalized_data.get("text_random_int_range")
        ),
        "option_fill_texts": normalized_data.get("option_fill_texts"),
        "fillable_option_indices": normalized_data.get("fillable_option_indices"),
        "attached_option_selects": _as_list(normalized_data.get("attached_option_selects")),
        "is_location": bool(normalized_data.get("is_location")),
        "location_parts": _as_list(normalized_data.get("location_parts")),
        "dimension": _normalize_dimension_value(normalized_data.get("dimension")),
    })

    try:
        return QuestionEntry.model_validate(normalized_data)
    except Exception as exc:
        raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：{exc}") from exc


def clone_question_entries(entries: list[object] | list[QuestionEntry] | None) -> list[QuestionEntry]:
    cloned: list[QuestionEntry] = []
    for item in list(entries or []):
        try:
            cloned.append(deserialize_question_entry(serialize_question_entry(item)))
        except Exception as exc:
            logger.info(f"跳过无法复制的题目配置: {exc}")
    return cloned


def survey_questions_from_definition(
    questions: list[SurveyQuestionMeta],
) -> list[QuestionInfo]:
    """从解析得到的题目元数据抽取极简题目消息，供持久化与后续网站使用。

    仅保留识别题目所需的字段（题号、标题、题型、选项、是否必填），
    不含运行时提交所需的 provider 内部 ID 等细节。
    """
    infos: list[QuestionInfo] = []
    for item in questions:
        infos.append(
            QuestionInfo(
                num=int(item.num or 0),
                title=item.title or "",
                question_type=str(item.type_code),
                options=list(getattr(item, "option_texts", None) or []),
                required=bool(item.required),
            )
        )
    return infos


def build_runtime_config_snapshot(
    config: RuntimeConfig,
    *,
    question_entries: list[object] | None = None,
) -> RuntimeConfig:
    snapshot = copy.deepcopy(config)
    default_provider = normalize_survey_provider(
        snapshot.survey.survey_provider,
        default=detect_survey_provider(snapshot.survey.url),
    )
    snapshot.survey.survey_provider = default_provider
    entry_source = (
        question_entries if question_entries is not None else snapshot.answer_config.question_entries
    )
    snapshot.answer_config.question_entries = clone_question_entries(entry_source)
    snapshot.answer_config.answer_rules = copy.deepcopy(
        list(snapshot.answer_config.answer_rules or [])
    )
    snapshot.execution.user_agent_ratios = copy.deepcopy(
        dict(snapshot.execution.user_agent_ratios or {})
    )
    return snapshot


def _validate_no_unknown_keys(raw: dict[str, object]) -> None:
    unknown_top_keys = set(raw or {}) - _SECTION_KEYS
    if unknown_top_keys:
        raise ValueError(
            f"{_CONFIG_CORRUPTED_MESSAGE}：配置包含不支持的顶级字段（{', '.join(sorted(unknown_top_keys))}）"
        )
    for section_key, allowed_fields in (
        ("survey", _SURVEY_SECTION_FIELDS),
        ("execution", _EXECUTION_SECTION_FIELDS),
        ("answer_config", _ANSWER_CONFIG_SECTION_FIELDS),
    ):
        section_raw = raw.get(section_key) or {}
        if not isinstance(section_raw, dict):
            raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：'{section_key}' 必须是字典类型")
        unknown = set(section_raw) - allowed_fields
        if unknown:
            raise ValueError(
                f"{_CONFIG_CORRUPTED_MESSAGE}：'{section_key}' 包含不支持的字段"
                f"（{', '.join(sorted(unknown))}）"
            )


def _normalize_question_entries_list(
    raw_entries: list[dict[str, object]],
    survey_provider: str,
) -> list[QuestionEntry]:
    entries: list[QuestionEntry] = []
    for item in raw_entries:
        entry = deserialize_question_entry(item)
        if (
            survey_provider != SURVEY_PROVIDER_WJX
            and entry.provider_question_id
            and normalize_survey_provider(entry.survey_provider) == SURVEY_PROVIDER_WJX
        ):
            entry.survey_provider = survey_provider
        entries.append(entry)
    return entries


def normalize_runtime_config_payload(raw: dict[str, object]) -> RuntimeConfig:
    _validate_no_unknown_keys(raw)

    # Extract fields that need special handling before Pydantic validation
    answer_config_raw = cast("dict[str, object]", raw.get("answer_config") or {})

    # Remove question_entries from raw to avoid validation errors
    raw_copy = dict(raw)
    if "answer_config" in raw_copy and isinstance(raw_copy["answer_config"], dict):
        answer_config_copy = dict(raw_copy["answer_config"])
        answer_config_copy.pop("question_entries", None)
        raw_copy["answer_config"] = answer_config_copy

    # Let Pydantic validate the structure and apply validators
    config = RuntimeConfig.model_validate(raw_copy)

    # Post-process question_entries (needs provider context)
    raw_question_entries = answer_config_raw.get("question_entries") or []
    question_entry_dicts: list[dict[str, object]] = [
        {str(k): v for k, v in item.items()}
        for item in raw_question_entries
        if isinstance(item, dict)
    ] if isinstance(raw_question_entries, list) else []
    config.answer_config.question_entries = _normalize_question_entries_list(
        question_entry_dicts,
        config.survey.survey_provider,
    )

    # Normalize answer_rules from raw YAML data
    raw_rules = answer_config_raw.get("answer_rules")
    normalized_rules: list[dict[str, Any]] = []
    if isinstance(raw_rules, list):
        for item in raw_rules:
            if isinstance(item, dict):
                rule_dict: dict[str, object] = {str(k): v for k, v in item.items()}
                normalized_rule = normalize_rule_dict(rule_dict)
                if normalized_rule:
                    normalized_rules.append(normalized_rule)
    config.answer_config.answer_rules = normalized_rules

    # Sanitize answer_rules (persisted config has no questions_info context)
    config.answer_config.answer_rules, _ = sanitize_answer_rules(
        config.answer_config.answer_rules,
        None,
    )

    return config


def _ensure_supported_config_payload(
    payload: dict[str, object], *, config_path: str
) -> dict[str, object]:
    del config_path
    return dict(payload)


def serialize_runtime_config(config: RuntimeConfig) -> dict[str, object]:
    payload: dict[str, object] = config.model_dump()
    answer_config_payload = cast("dict[str, object]", payload["answer_config"])
    answer_config_payload["question_entries"] = [
        serialize_question_entry(entry)
        for entry in list(config.answer_config.question_entries or [])
    ]
    return payload


def deserialize_runtime_config(payload: dict[str, object]) -> RuntimeConfig:
    return normalize_runtime_config_payload(payload)
