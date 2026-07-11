from __future__ import annotations

import copy
import logging
import random

from pydantic import ConfigDict

from survey_submitter.core.reverse_fill import REVERSE_FILL_FORMAT_AUTO, REVERSE_FILL_FORMAT_WJX_SCORE, REVERSE_FILL_FORMAT_WJX_SEQUENCE, REVERSE_FILL_FORMAT_WJX_TEXT
from survey_submitter.core.config.answer_datetime_window import normalize_answer_datetime_window
from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.config.schema import RuntimeConfig
from survey_submitter.core.psychometrics.psychometric import normalize_target_alpha
from survey_submitter.core.questions.consistency import normalize_rule_dict, sanitize_answer_rules
from survey_submitter.core.questions.utils import serialize_random_int_range
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    ensure_questions_provider_fields,
    normalize_survey_provider,
)
from survey_submitter.providers.contracts import (
    SurveyQuestionMeta,
    clone_survey_question_metas,
    ensure_survey_question_metas,
    serialize_survey_question_metas,
)
from survey_submitter.logging.log_utils import log_suppressed_exception
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
    "clone_questions_info",
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
    "psycho_bias",
}

_SURVEY_QUESTION_META_FIELDS = {
    "num",
    "title",
    "type_code",
    "required",
    "description",
    "unsupported",
    "unsupported_reason",
    "display_num",
    "has_jump",
    "jump_rules",
    "has_display_condition",
    "display_conditions",
    "has_dependent_display_logic",
    "controls_display_targets",
    "logic_parse_status",
    "question_media",
    "option_texts",
    "forced_option_index",
    "forced_option_text",
    "fillable_options",
    "attached_option_selects",
    "has_attached_option_select",
    "multi_min_limit",
    "multi_max_limit",
    "rows",
    "row_texts",
    "rating_max",
    "text_inputs",
    "text_input_labels",
    "is_location",
    "slider_min",
    "slider_max",
    "slider_step",
    "page",
    "provider",
    "provider_question_id",
    "provider_page_id",
    "provider_type",
    "provider_page_raw",
    "options",
    "forced_texts",
    "is_rating",
    "is_description",
    "is_multi_text",
    "is_text_like",
    "is_slider_matrix",
}

_RUNTIME_CONFIG_FIELDS = set(RuntimeConfig.model_fields.keys())
_RUNTIME_CONFIG_FIELDS.update({"question_entries", "questions_info"})


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


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


def _normalize_user_agent_ratios(raw_ratios: object) -> dict[str, int]:
    if not isinstance(raw_ratios, dict):
        return dict(_DEFAULT_RANDOM_UA_RATIOS)

    ratios: dict[str, int] = {}
    for device_type in _DEFAULT_RANDOM_UA_RATIOS:
        value = _coerce_int(raw_ratios.get(device_type), 0)
        if value < 0 or value > 100:
            return dict(_DEFAULT_RANDOM_UA_RATIOS)
        ratios[device_type] = value

    if sum(ratios.values()) != 100:
        return dict(_DEFAULT_RANDOM_UA_RATIOS)
    return ratios


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
            if float(item) > 0:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _normalize_psycho_bias(data: dict[str, object]) -> str:
    bias = str(data.get("psycho_bias") or "custom")
    if bias in ("left", "center", "right"):
        return bias
    return "custom"


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


def _normalize_answer_duration_range(value: object) -> tuple[int, int]:
    if value in (None, "", []):
        return DEFAULT_ANSWER_DURATION_RANGE_SECONDS
    try:
        if isinstance(value, (list, tuple)):
            if len(value) >= 2:
                low = min(MAX_ANSWER_DURATION_SECONDS, max(0, int(value[0])))
                high = min(MAX_ANSWER_DURATION_SECONDS, max(low, int(value[1])))
                if low == 0 and high == 0:
                    return DEFAULT_ANSWER_DURATION_RANGE_SECONDS
                if low == high:
                    return _legacy_answer_duration_to_range(low)
                return low, high
            if len(value) == 1:
                return _legacy_answer_duration_to_range(int(value[0]))
            return DEFAULT_ANSWER_DURATION_RANGE_SECONDS
        return _legacy_answer_duration_to_range(int(value))
    except (ValueError, TypeError) as exc:
        log_suppressed_exception(
            "_normalize_answer_duration_range failure",
            exc,
            level=logging.WARNING,
        )
        return DEFAULT_ANSWER_DURATION_RANGE_SECONDS


def _normalize_dimension_value(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text or text == "未分组":
        return None
    return text


def _normalize_dimension_groups(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    groups: list[str] = []
    seen = set()
    for item in raw:
        normalized = _normalize_dimension_value(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        groups.append(normalized)
    return groups


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
        "multi_text_blank_ai_flags": _normalize_multi_text_blank_ai_flags(entry.multi_text_blank_ai_flags),
        "multi_text_blank_int_ranges": _normalize_multi_text_blank_int_ranges(entry.multi_text_blank_int_ranges),
        "text_random_mode": str(entry.text_random_mode or "none"),
        "text_random_int_range": _normalize_random_int_range(entry.text_random_int_range),
        "option_fill_texts": entry.option_fill_texts,
        "fillable_option_indices": entry.fillable_option_indices,
        "attached_option_selects": list(entry.attached_option_selects or []),
        "is_location": entry.is_location,
        "location_parts": list(entry.location_parts or []),
        "dimension": _normalize_dimension_value(entry.dimension),
        "psycho_bias": str(entry.psycho_bias or "custom"),
    }


def deserialize_question_entry(data: dict[str, object]):
    from survey_submitter.core.questions.config import QuestionEntry

    def _as_int(value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    unknown_keys = set(data or {}) - _QUESTION_ENTRY_FIELDS
    if unknown_keys:
        raise ValueError(f"{_CONFIG_CORRUPTED_MESSAGE}：题目配置包含不支持的字段（{', '.join(sorted(unknown_keys))}）")
    mode_raw = data.get("distribution_mode") or "random"
    probabilities = data.get("probabilities")
    custom_weights = data.get("custom_weights")
    if mode_raw == "custom" and _prob_config_is_unset(probabilities) and _custom_weights_has_positive(custom_weights):
        probabilities = custom_weights
    if mode_raw == "custom" and (custom_weights is None or custom_weights == []) and isinstance(probabilities, list):
        custom_weights = list(probabilities)
    return QuestionEntry(
        question_type=data.get("question_type") or "text",
        probabilities=probabilities,
        texts=data.get("texts"),
        rows=_as_int(data.get("rows"), 1),
        option_count=_as_int(data.get("option_count"), 0),
        distribution_mode=mode_raw,
        custom_weights=custom_weights,
        question_num=data.get("question_num"),
        question_title=data.get("question_title"),
        survey_provider=normalize_survey_provider(
            data.get("survey_provider"),
            default=SURVEY_PROVIDER_WJX,
        ),
        provider_question_id=str(data.get("provider_question_id") or "").strip() or None,
        provider_page_id=str(data.get("provider_page_id") or "").strip() or None,
        ai_enabled=bool(data.get("ai_enabled", False)),
        multi_text_blank_modes=_normalize_multi_text_blank_modes(data.get("multi_text_blank_modes")),
        multi_text_blank_ai_flags=_normalize_multi_text_blank_ai_flags(data.get("multi_text_blank_ai_flags")),
        multi_text_blank_int_ranges=_normalize_multi_text_blank_int_ranges(data.get("multi_text_blank_int_ranges")),
        text_random_mode=str(data.get("text_random_mode") or "none"),
        text_random_int_range=_normalize_random_int_range(data.get("text_random_int_range")),
        option_fill_texts=data.get("option_fill_texts"),
        fillable_option_indices=data.get("fillable_option_indices"),
        attached_option_selects=list(data.get("attached_option_selects") or []),
        is_location=bool(data.get("is_location")),
        location_parts=list(data.get("location_parts") or []),
        dimension=_normalize_dimension_value(data.get("dimension")),
        psycho_bias=_normalize_psycho_bias(data),
    )


def clone_question_entries(entries: list[object] | None) -> list[object]:
    cloned: list[object] = []
    for item in list(entries or []):
        try:
            cloned.append(deserialize_question_entry(serialize_question_entry(item)))
        except Exception as exc:
            logging.info("跳过无法复制的题目配置: %s", exc)
    return cloned


def clone_questions_info(questions: list[SurveyQuestionMeta] | None, *, default_provider: str = SURVEY_PROVIDER_WJX) -> list[SurveyQuestionMeta]:
    return clone_survey_question_metas(questions or [], default_provider=default_provider)


def build_runtime_config_snapshot(
    config: RuntimeConfig,
    *,
    question_entries: list[object] | None = None,
    questions_info: list[SurveyQuestionMeta] | None = None,
) -> RuntimeConfig:
    snapshot = copy.deepcopy(config)
    default_provider = normalize_survey_provider(
        snapshot.survey_provider,
        default=detect_survey_provider(snapshot.url),
    )
    snapshot.survey_provider = default_provider
    entry_source = question_entries if question_entries is not None else snapshot.question_entries
    info_source = questions_info if questions_info is not None else snapshot.questions_info
    snapshot.question_entries = clone_question_entries(entry_source)
    snapshot.questions_info = clone_questions_info(info_source, default_provider=default_provider)
    snapshot.answer_rules = copy.deepcopy(list(snapshot.answer_rules or []))
    snapshot.dimension_groups = copy.deepcopy(list(snapshot.dimension_groups or []))
    snapshot.random_ua_ratios = copy.deepcopy(dict(snapshot.random_ua_ratios or {}))
    return snapshot


def _validate_no_unknown_keys(raw: dict[str, object]) -> None:
    unknown_keys = set(raw or {}) - _RUNTIME_CONFIG_FIELDS
    if unknown_keys:
        raise ValueError(
            f"{_CONFIG_CORRUPTED_MESSAGE}：配置包含不支持的字段（{', '.join(sorted(unknown_keys))}）"
        )


def _apply_basic_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.url = _as_str(raw.get("url"))
    config.survey_title = _as_str(raw.get("survey_title"))
    config.survey_provider = normalize_survey_provider(
        raw.get("survey_provider"),
        default=detect_survey_provider(config.url),
    )
    config.target = _coerce_int(raw.get("target"), 1)
    config.threads = _coerce_int(raw.get("threads"), 1)


def _normalize_submit_interval(raw_value: object) -> tuple[int, int]:
    try:
        if isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
            return int(raw_value[0]), int(raw_value[1])
        return (0, 0)
    except (ValueError, TypeError) as exc:
        log_suppressed_exception("_tuple_pair failure", exc, level=logging.WARNING)
        return (0, 0)


def _apply_timing_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.submit_interval = _normalize_submit_interval(raw.get("submit_interval"))
    config.answer_duration = _normalize_answer_duration_range(raw.get("answer_duration"))
    config.answer_datetime_window = normalize_answer_datetime_window(
        raw.get("answer_datetime_window")
    )


def _apply_proxy_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.custom_proxy_api = _as_str(raw.get("custom_proxy_api"))
    proxy_source = _as_str(raw.get("proxy_source"), "default").lower()
    if proxy_source not in ("default", "benefit", "custom"):
        proxy_source = "default"
    config.proxy_source = proxy_source
    config.random_ip_enabled = _as_bool(raw.get("random_ip_enabled"))
    raw_area_code = raw.get("proxy_area_code")
    config.proxy_area_code = None if raw_area_code is None else str(raw_area_code)


def _apply_ua_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.random_ua_enabled = _as_bool(raw.get("random_ua_enabled"))
    config.random_ua_ratios = _normalize_user_agent_ratios(raw.get("random_ua_ratios"))


def _apply_feature_flags(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.fail_stop_enabled = bool(raw.get("fail_stop_enabled", True))
    config.pause_on_aliyun_captcha = bool(raw.get("pause_on_aliyun_captcha", True))
    config.reliability_mode_enabled = bool(raw.get("reliability_mode_enabled", True))
    config.psycho_target_alpha = normalize_target_alpha(raw.get("psycho_target_alpha"))


def _apply_reverse_fill_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.reverse_fill_enabled = _as_bool(raw.get("reverse_fill_enabled"))
    config.reverse_fill_source_path = _as_str(raw.get("reverse_fill_source_path"))
    reverse_fill_format = _as_str(raw.get("reverse_fill_format"), REVERSE_FILL_FORMAT_AUTO).lower()
    config.reverse_fill_format = (
        reverse_fill_format if reverse_fill_format in _REVERSE_FILL_FORMATS else REVERSE_FILL_FORMAT_AUTO
    )
    config.reverse_fill_start_row = max(1, _coerce_int(raw.get("reverse_fill_start_row"), 1))
    config.reverse_fill_threads = max(1, _coerce_int(raw.get("reverse_fill_threads"), config.threads or 1))


def _apply_answer_rules(config: RuntimeConfig, raw: dict[str, object]) -> None:
    config.answer_rules = []
    raw_rules = raw.get("answer_rules")
    if isinstance(raw_rules, list):
        for item in raw_rules:
            normalized_rule = normalize_rule_dict(item)
            if normalized_rule:
                config.answer_rules.append(normalized_rule)


def _apply_ai_settings(config: RuntimeConfig, raw: dict[str, object]) -> None:
    ai_keys = {
        "ai_api_key",
        "ai_base_url",
        "ai_api_protocol",
        "ai_model",
        "ai_system_prompt",
    }
    if not any(key in raw for key in ai_keys):
        return
    config.ai_api_key = _as_str(raw.get("ai_api_key"))
    config.ai_base_url = _as_str(raw.get("ai_base_url"))
    config.ai_api_protocol = _as_str(raw.get("ai_api_protocol"), "auto")
    config.ai_model = _as_str(raw.get("ai_model"))
    config.ai_system_prompt = _as_str(raw.get("ai_system_prompt"))


def _normalize_question_entries_list(
    raw_entries: list[dict[str, object]],
    survey_provider: str,
) -> list[object]:
    entries: list[object] = []
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


def _normalize_questions_info_data(
    raw_data: object,
    survey_provider: str,
) -> list[SurveyQuestionMeta]:
    if not isinstance(raw_data, list):
        return []
    for item in raw_data:
        if isinstance(item, dict):
            unknown_question_keys = set(item) - _SURVEY_QUESTION_META_FIELDS
            if unknown_question_keys:
                raise ValueError(
                    f"{_CONFIG_CORRUPTED_MESSAGE}：题目元数据包含不支持的字段（"
                    f"{', '.join(sorted(unknown_question_keys))}）"
                )
    normalized_questions = ensure_questions_provider_fields(
        raw_data,
        default_provider=survey_provider,
    )
    return ensure_survey_question_metas(
        normalized_questions,
        default_provider=survey_provider,
    )


def normalize_runtime_config_payload(raw: dict[str, object]) -> RuntimeConfig:
    _validate_no_unknown_keys(raw)
    config = RuntimeConfig()

    _apply_basic_settings(config, raw)
    _apply_timing_settings(config, raw)
    _apply_proxy_settings(config, raw)
    _apply_ua_settings(config, raw)
    _apply_feature_flags(config, raw)
    _apply_reverse_fill_settings(config, raw)
    _apply_answer_rules(config, raw)
    config.dimension_groups = _normalize_dimension_groups(raw.get("dimension_groups"))
    _apply_ai_settings(config, raw)

    config.question_entries = _normalize_question_entries_list(
        raw.get("question_entries") or [],
        config.survey_provider,
    )
    config.questions_info = _normalize_questions_info_data(
        raw.get("questions_info"),
        config.survey_provider,
    )

    config.answer_rules, _ = sanitize_answer_rules(config.answer_rules, config.questions_info or [])
    return config


def _ensure_supported_config_payload(payload: dict[str, object], *, config_path: str) -> dict[str, object]:
    del config_path
    return dict(payload)


def serialize_runtime_config(config: RuntimeConfig) -> dict[str, object]:
    payload: dict[str, object] = config.model_dump()
    payload["question_entries"] = [
        serialize_question_entry(entry) for entry in list(config.question_entries or [])
    ]
    payload["questions_info"] = serialize_survey_question_metas(config.questions_info or [])
    return payload


def deserialize_runtime_config(payload: dict[str, object]) -> RuntimeConfig:
    return normalize_runtime_config_payload(payload)


