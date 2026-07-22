from __future__ import annotations

import copy
import random
from loguru import logger
from typing import Any, cast

from pydantic import ConfigDict

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.schema import (
    ChoiceQuestionAnswerConfig,
    LocationQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    QuestionAnswerConfig,
    QuestionDetail,
    TextQuestionAnswerConfig,
    answer_config_type_for_question_type,
)
from survey_submitter.core.questions.types import QuestionType
from survey_submitter.core.config.schema import (
    AnswerConfigSection,
    AnswerRulesConfig,
    ExecutionSection,
    QuestionInfo,
    RuntimeConfig,
    SurveySection,
)
from survey_submitter.core.questions.consistency import normalize_rule_dict, sanitize_answer_rules
from survey_submitter.core.questions.utils import serialize_random_int_range
from survey_submitter.providers.common import (
    detect_survey_provider,
    normalize_survey_provider,
)
from survey_submitter.providers.contracts import SurveyQuestionMeta
from survey_submitter.constants import USER_AGENT_PRESETS

_TEXT_RANDOM_MODES = {"none", "name", "mobile", "id_card", "integer"}
DEFAULT_ANSWER_DURATION_RANGE_SECONDS = (60, 120)
MAX_ANSWER_DURATION_SECONDS = 30 * 60
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
    "serialize_question_detail",
    "deserialize_question_detail",
    "clone_question_details",
    "survey_questions_from_definition",
    "build_runtime_config_snapshot",
    "normalize_runtime_config_payload",
    "serialize_runtime_config",
    "deserialize_runtime_config",
    "_ensure_supported_config_payload",
]

_CONFIG_CORRUPTED_MESSAGE = "该配置文件损坏，请输入问卷链接/二维码重新配置"
CURRENT_CONFIG_SCHEMA_VERSION = 7

_QUESTION_DETAIL_FIELDS = {
    "provider_question_id",
    "provider_page_id",
    "probabilities",
    "distribution_mode",
    "custom_weights",
    "dimension",
    "answer_config",
}

_ANSWER_CONFIG_FIELDS = {
    "ai_enabled",
    "option_fill_texts",
    "fillable_option_indices",
    "attached_option_selects",
    "text_random_mode",
    "text_random_int_range",
    "multi_text_blank_modes",
    "multi_text_blank_ai_flags",
    "multi_text_blank_int_ranges",
    "location_parts",
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


def serialize_question_detail(qi: QuestionInfo) -> dict[str, object]:
    detail = qi.details
    ac = detail.answer_config
    payload: dict[str, object] = {
        "num": qi.num,
        "title": qi.title,
        "question_type": qi.question_type,
        "options": list(qi.options),
        "required": qi.required,
        "details": {
            "provider_question_id": str(detail.provider_question_id or ""),
            "provider_page_id": str(detail.provider_page_id or ""),
            "probabilities": detail.probabilities,
            "distribution_mode": detail.distribution_mode,
            "custom_weights": detail.custom_weights,
            "dimension": _normalize_dimension_value(detail.dimension),
            "answer_config": {},
        },
    }
    ac_dict: dict[str, object] = {}
    if isinstance(ac, ChoiceQuestionAnswerConfig):
        ac_dict["ai_enabled"] = bool(ac.ai_enabled)
        if ac.option_fill_texts is not None:
            ac_dict["option_fill_texts"] = ac.option_fill_texts
        if ac.fillable_option_indices:
            ac_dict["fillable_option_indices"] = ac.fillable_option_indices
        if ac.attached_option_selects:
            ac_dict["attached_option_selects"] = list(ac.attached_option_selects)
    elif isinstance(ac, TextQuestionAnswerConfig):
        ac_dict = {
            "ai_enabled": bool(ac.ai_enabled),
            "text_random_mode": str(ac.text_random_mode or "none"),
            "text_random_int_range": _normalize_random_int_range(ac.text_random_int_range),
        }
    elif isinstance(ac, MultiTextQuestionAnswerConfig):
        ac_dict = {
            "ai_enabled": bool(ac.ai_enabled),
            "multi_text_blank_modes": _normalize_multi_text_blank_modes(ac.multi_text_blank_modes),
            "multi_text_blank_ai_flags": _normalize_multi_text_blank_ai_flags(
                ac.multi_text_blank_ai_flags
            ),
            "multi_text_blank_int_ranges": _normalize_multi_text_blank_int_ranges(
                ac.multi_text_blank_int_ranges
            ),
        }
    elif isinstance(ac, LocationQuestionAnswerConfig):
        ac_dict = {
            "ai_enabled": bool(ac.ai_enabled),
            "location_parts": list(ac.location_parts or []),
        }
    else:
        ac_dict = {"ai_enabled": bool(ac.ai_enabled)}
    payload["details"]["answer_config"] = ac_dict  # type: ignore[index]
    return payload


_QUESTION_INFO_FIELDS = frozenset(
    {"num", "title", "question_type", "options", "required", "details"}
)
_QUESTION_DETAIL_FIELDS = frozenset(
    {
        "provider_question_id",
        "provider_page_id",
        "probabilities",
        "distribution_mode",
        "custom_weights",
        "dimension",
        "answer_config",
    }
)
_ANSWER_CONFIG_FIELDS = frozenset({"ai_enabled"})


def deserialize_question_detail(data: dict[str, object]) -> QuestionInfo:
    if not isinstance(data, dict):
        raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：题目配置必须是字典类型")

    unknown = set(data) - _QUESTION_INFO_FIELDS
    if unknown:
        raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：题目配置包含未知字段 {sorted(unknown)}")

    num = _coerce_int(data.get("num"), 0)
    title = str(data.get("title") or "").strip()
    question_type = _normalize_question_type(data.get("question_type"))
    options = _as_list(data.get("options")) if isinstance(data.get("options"), list) else []
    required = _as_bool(data.get("required"))

    raw_details = data.get("details")
    detail_raw: dict[str, object] = (
        cast("dict[str, object]", raw_details) if isinstance(raw_details, dict) else {}
    )
    unknown_detail = set(detail_raw) - _QUESTION_DETAIL_FIELDS
    if unknown_detail:
        raise ValueError(
            f"{_CONFIG_CORRUPTED_MESSAGE}：题目详情包含未知字段 {sorted(unknown_detail)}"
        )

    mode_raw = str(detail_raw.get("distribution_mode") or "random").strip()
    probabilities: object = detail_raw.get("probabilities")
    custom_weights: object = detail_raw.get("custom_weights")
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

    raw_ac = detail_raw.get("answer_config")
    ac_raw: dict[str, object] = (
        cast("dict[str, object]", raw_ac) if isinstance(raw_ac, dict) else {}
    )
    ac_location_parts = ac_raw.get("location_parts")
    location_parts: list[str] = (
        [str(p) for p in ac_location_parts]
        if isinstance(ac_location_parts, list)
        else [str(p) for p in _as_list(detail_raw.get("location_parts"))]
    )
    ac_cls = answer_config_type_for_question_type(
        question_type, location_parts=location_parts or None
    )

    ac_fields = dict(ac_raw)
    if ac_cls is ChoiceQuestionAnswerConfig:
        answer_config = ChoiceQuestionAnswerConfig(
            ai_enabled=_as_bool(ac_fields.get("ai_enabled")),
            option_fill_texts=cast("list[str | None] | None", ac_fields.get("option_fill_texts")),
            fillable_option_indices=cast(
                "list[int] | None", ac_fields.get("fillable_option_indices")
            ),
            attached_option_selects=cast(
                "list[dict[str, object]]", _as_list(ac_fields.get("attached_option_selects"))
            ),
        )
    elif ac_cls is TextQuestionAnswerConfig:
        answer_config = TextQuestionAnswerConfig(
            ai_enabled=_as_bool(ac_fields.get("ai_enabled")),
            text_random_mode=str(ac_fields.get("text_random_mode") or "none").strip(),
            text_random_int_range=_normalize_random_int_range(
                ac_fields.get("text_random_int_range")
            ),
        )
    elif ac_cls is MultiTextQuestionAnswerConfig:
        answer_config = MultiTextQuestionAnswerConfig(
            ai_enabled=_as_bool(ac_fields.get("ai_enabled")),
            multi_text_blank_modes=_normalize_multi_text_blank_modes(
                ac_fields.get("multi_text_blank_modes")
            ),
            multi_text_blank_ai_flags=_normalize_multi_text_blank_ai_flags(
                ac_fields.get("multi_text_blank_ai_flags")
            ),
            multi_text_blank_int_ranges=_normalize_multi_text_blank_int_ranges(
                ac_fields.get("multi_text_blank_int_ranges")
            ),
        )
    elif ac_cls is LocationQuestionAnswerConfig:
        answer_config = LocationQuestionAnswerConfig(
            ai_enabled=_as_bool(ac_fields.get("ai_enabled")),
            location_parts=cast("list[str]", _as_list(ac_fields.get("location_parts"))),
        )
    else:
        answer_config = QuestionAnswerConfig(ai_enabled=_as_bool(ac_fields.get("ai_enabled")))

    detail = QuestionDetail(
        provider_question_id=str(detail_raw.get("provider_question_id") or "").strip() or None,
        provider_page_id=str(detail_raw.get("provider_page_id") or "").strip() or None,
        probabilities=cast("list[float] | list[list[float]] | int | None", probabilities),
        distribution_mode=mode_raw,
        custom_weights=cast("list[float] | list[list[float]] | None", custom_weights),
        dimension=_normalize_dimension_value(detail_raw.get("dimension")),
        answer_config=answer_config,
    )

    return QuestionInfo(
        num=num,
        title=title,
        question_type=question_type,
        options=[str(o) for o in options],
        required=required,
        details=detail,
    )


def clone_question_details(
    questions: list[QuestionInfo] | None,
) -> list[QuestionInfo]:
    cloned: list[QuestionInfo] = []
    for item in list(questions or []):
        try:
            cloned.append(deserialize_question_detail(serialize_question_detail(item)))
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
    survey_questions: list[QuestionInfo] | None = None,
) -> RuntimeConfig:
    snapshot = copy.deepcopy(config)
    default_provider = normalize_survey_provider(
        snapshot.survey.provider,
        default=detect_survey_provider(snapshot.survey.url),
    )
    snapshot.survey.provider = default_provider
    questions_source = (
        survey_questions
        if survey_questions is not None
        else snapshot.answer_config.survey_questions
    )
    snapshot.answer_config.survey_questions = clone_question_details(questions_source)
    snapshot.answer_config.answer_rules = copy.deepcopy(snapshot.answer_config.answer_rules)
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


def _normalize_question_details_list(
    raw_questions: list[dict[str, object]],
) -> list[QuestionInfo]:
    details: list[QuestionInfo] = []
    for item in raw_questions:
        details.append(deserialize_question_detail(item))
    return details


def normalize_runtime_config_payload(raw: dict[str, object]) -> RuntimeConfig:
    _validate_no_unknown_keys(raw)

    answer_config_raw = cast("dict[str, object]", raw.get("answer_config") or {})

    raw_copy = dict(raw)
    if "answer_config" in raw_copy and isinstance(raw_copy["answer_config"], dict):
        answer_config_copy = dict(raw_copy["answer_config"])
        answer_config_copy.pop("survey_questions", None)
        # Convert legacy flat list answer_rules to AnswerRulesConfig
        raw_answer_rules = answer_config_copy.get("answer_rules")
        if isinstance(raw_answer_rules, list):
            answer_config_copy["answer_rules"] = {"constraints": raw_answer_rules}
        raw_copy["answer_config"] = answer_config_copy

    config = RuntimeConfig.model_validate(raw_copy)

    raw_survey_questions = answer_config_raw.get("survey_questions") or []
    question_info_dicts: list[dict[str, object]] = (
        [
            {str(k): v for k, v in item.items()}
            for item in raw_survey_questions
            if isinstance(item, dict)
        ]
        if isinstance(raw_survey_questions, list)
        else []
    )
    config.answer_config.survey_questions = _normalize_question_details_list(
        question_info_dicts,
    )

    raw_rules = answer_config_raw.get("answer_rules")
    if isinstance(raw_rules, dict):
        raw_constraints = raw_rules.get("constraints")
        raw_per_question = raw_rules.get("per_question")
        normalized_constraints: list[dict[str, Any]] = []
        if isinstance(raw_constraints, list):
            for item in raw_constraints:
                if isinstance(item, dict):
                    rule_dict: dict[str, object] = {str(k): v for k, v in item.items()}
                    normalized_rule = normalize_rule_dict(rule_dict)
                    if normalized_rule:
                        normalized_constraints.append(normalized_rule)
        normalized_per_question: list[dict[str, Any]] = []
        if isinstance(raw_per_question, list):
            for item in raw_per_question:
                if isinstance(item, dict):
                    rule_dict = {str(k): v for k, v in item.items()}
                    normalized_rule = normalize_rule_dict(rule_dict)
                    if normalized_rule:
                        normalized_per_question.append(normalized_rule)
        config.answer_config.answer_rules = AnswerRulesConfig(
            constraints=normalized_constraints,
            per_question=normalized_per_question,
        )
    elif isinstance(raw_rules, list):
        normalized_constraints = []
        normalized_per_question = []
        for item in raw_rules:
            if isinstance(item, dict):
                rule_dict = {str(k): v for k, v in item.items()}
                normalized_rule = normalize_rule_dict(rule_dict)
                if normalized_rule:
                    normalized_constraints.append(normalized_rule)
        config.answer_config.answer_rules = AnswerRulesConfig(
            constraints=normalized_constraints,
            per_question=normalized_per_question,
        )

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


def serialize_runtime_config(config: RuntimeConfig) -> dict[str, Any]:
    payload: dict[str, Any] = config.model_dump()
    answer_config_payload = cast("dict[str, Any]", payload["answer_config"])
    answer_config_payload["survey_questions"] = [
        serialize_question_detail(qi) for qi in list(config.answer_config.survey_questions or [])
    ]
    return payload


def deserialize_runtime_config(payload: dict[str, object]) -> RuntimeConfig:
    return normalize_runtime_config_payload(payload)
