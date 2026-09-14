from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, TypedDict, cast

from pydantic import field_serializer, model_validator

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.types import QuestionType, convert_wire_type_code
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX, normalize_survey_provider


class JumpRule(TypedDict, total=False):
    option_index: int
    jumpto: int
    option_text: str | None
    terminates_survey: bool


class DisplayCondition(TypedDict, total=False):
    condition_question_num: int
    condition_mode: str
    condition_option_indices: list[int]
    raw_relation: str | None
    target_question_num: int


class QuestionMedia(TypedDict, total=False):
    kind: str
    scope: str
    index: int | None
    source_url: str
    label: str


class AttachedOptionSelect(TypedDict, total=False):
    option_index: int
    option_text: str
    select_options: list[str]
    weights: list[float] | None

__all__ = [
    "LOGIC_PARSE_STATUS_COMPLETE",
    "LOGIC_PARSE_STATUS_NONE",
    "LOGIC_PARSE_STATUS_UNKNOWN",
    "AttachedOptionSelect",
    "ChoiceQuestionMeta",
    "DisplayCondition",
    "JumpRule",
    "MatrixQuestionMeta",
    "MultipleChoiceQuestionMeta",
    "QuestionMedia",
    "QuestionSignal",
    "RatingQuestionMeta",
    "SingleChoiceQuestionMeta",
    "SliderQuestionMeta",
    "SurveyDefinition",
    "SurveyQuestionMeta",
    "TextQuestionMeta",
    "build_survey_definition",
    "clone_survey_question_metas",
    "ensure_survey_question_meta",
    "ensure_survey_question_metas",
    "normalize_survey_questions",
    "serialize_survey_question_metas",
    "survey_question_meta_to_dict",
]

LOGIC_PARSE_STATUS_COMPLETE = "complete"
LOGIC_PARSE_STATUS_NONE = "none"
LOGIC_PARSE_STATUS_UNKNOWN = "unknown"
_VALID_LOGIC_PARSE_STATUSES = {
    LOGIC_PARSE_STATUS_COMPLETE,
    LOGIC_PARSE_STATUS_NONE,
    LOGIC_PARSE_STATUS_UNKNOWN,
}


def _normalize_text_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) if item else "" for item in raw]


def _normalize_dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, object]] = []
    for item in raw:
        normalized = _survey_question_input_to_dict(item)
        if normalized is not None:
            items.append(normalized)
    return items


_ATTACHED_OPTION_KEYS = frozenset({"option_index", "option_text", "select_options", "weights"})


def _filter_attached_items(attached_list: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop non-contract keys such as the html-parser debug field ``select_option_count``."""
    return [
        {key: value for key, value in item.items() if key in _ATTACHED_OPTION_KEYS}
        for item in attached_list
    ]


def _normalize_jump_rules(raw: object) -> list[JumpRule]:
    rules = _normalize_dict_list(raw)
    normalized_rules: list[JumpRule] = []
    terminate_keywords = ("结束作答", "结束答题", "结束填写", "终止作答", "停止作答")
    for rule in rules:
        normalized_rule = dict(rule)
        if "terminates_survey" not in normalized_rule:
            option_text = str(normalized_rule.get("option_text") or "")
            normalized_rule["terminates_survey"] = bool(
                option_text and any(keyword in option_text for keyword in terminate_keywords)
            )
        else:
            normalized_rule["terminates_survey"] = bool(normalized_rule["terminates_survey"])
        normalized_rules.append(cast(JumpRule, normalized_rule))
    return normalized_rules


def _signal_hit(normalized: Mapping[str, object], signal: QuestionSignal, legacy_key: str) -> bool:
    """输入 dict 里信号是否命中（新格式读 signals 键，旧格式读 is_/has_ 布尔键）。"""
    raw_signals = normalized.get("signals") or ()
    if isinstance(raw_signals, (list, tuple, set, frozenset)):
        for value in cast(Iterable[object], raw_signals):
            if isinstance(value, str) and value == signal.value:
                return True
    return bool(normalized.get(legacy_key))


def _infer_logic_parse_status(normalized: Mapping[str, object]) -> str:
    if "logic_parse_status" in normalized:
        explicit = str(normalized.get("logic_parse_status") or "").lower()
        if explicit in _VALID_LOGIC_PARSE_STATUSES:
            return explicit
        return LOGIC_PARSE_STATUS_UNKNOWN

    has_logic = (
        _signal_hit(normalized, QuestionSignal.JUMP, "has_jump")
        or _signal_hit(normalized, QuestionSignal.DISPLAY_CONDITION, "has_display_condition")
        or _signal_hit(
            normalized, QuestionSignal.DEPENDENT_DISPLAY_LOGIC, "has_dependent_display_logic"
        )
    )
    if not has_logic:
        return LOGIC_PARSE_STATUS_NONE

    parsed_logic = bool(
        _normalize_dict_list(normalized.get("jump_rules"))
        or _normalize_dict_list(normalized.get("display_conditions"))
        or _normalize_dict_list(normalized.get("controls_display_targets"))
    )
    return LOGIC_PARSE_STATUS_COMPLETE if parsed_logic else LOGIC_PARSE_STATUS_UNKNOWN


def _normalize_question_media_list(raw: object) -> list[QuestionMedia]:
    if not isinstance(raw, list):
        return []
    items: list[QuestionMedia] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").lower()
        if kind != "image":
            continue
        scope = str(item.get("scope") or "").lower()
        if scope not in {"title", "option", "row"}:
            continue
        source_url = str(item.get("source_url") or "")
        if not source_url:
            continue
        index = item.get("index")
        if scope == "title":
            normalized_index = None
        else:
            if index is None:
                continue
            try:
                normalized_index = int(cast("int | str", index))
            except (ValueError, TypeError):
                continue
            if normalized_index < 0:
                continue
        label = str(item.get("label") or "")
        items.append(
            {
                "kind": "image",
                "scope": scope,
                "index": normalized_index,
                "source_url": source_url,
                "label": label,
            }
        )
    return items


class QuestionSignal(StrEnum):
    """题目携带的布尔信号。

    取代旧契约里堆砌的 is_/has_ 布尔字段：序列化成
    ``"signals": ["jump", "required"]``，旧格式的布尔键由
    :meth:`SurveyQuestionMeta` 的前置校验器折叠吸收。
    """

    REQUIRED = "required"
    UNSUPPORTED = "unsupported"
    JUMP = "jump"
    DISPLAY_CONDITION = "display-condition"
    DEPENDENT_DISPLAY_LOGIC = "dependent-display-logic"
    ATTACHED_OPTION_SELECT = "attached-option-select"
    LOCATION = "location"


# 旧契约的 is_/has_ 布尔键 → 信号成员（读侧兼容存量配置）
_LEGACY_BOOL_KEYS: Mapping[str, QuestionSignal] = {
    "required": QuestionSignal.REQUIRED,
    "unsupported": QuestionSignal.UNSUPPORTED,
    "has_jump": QuestionSignal.JUMP,
    "has_display_condition": QuestionSignal.DISPLAY_CONDITION,
    "has_dependent_display_logic": QuestionSignal.DEPENDENT_DISPLAY_LOGIC,
    "has_attached_option_select": QuestionSignal.ATTACHED_OPTION_SELECT,
    "is_location": QuestionSignal.LOCATION,
}
_SIGNAL_VALUES = frozenset(signal.value for signal in QuestionSignal)

# 题型家族：保持旧实现 _build_text_kwargs / _build_choice_kwargs 的分支边界
_TEXT_FAMILY_TYPES = frozenset(
    {QuestionType.TEXT, QuestionType.MULTI_TEXT, QuestionType.LOCATION}
)
_NON_CHOICE_FAMILY_TYPES = (
    _TEXT_FAMILY_TYPES
    | {
        QuestionType.DESCRIPTION,
        QuestionType.MATRIX,
        QuestionType.SCORE,
        QuestionType.SCALE,
        QuestionType.SLIDER,
    }
)


class SurveyQuestionMeta(BaseConfigModel):
    num: int
    title: str
    type_code: QuestionType = QuestionType.UNKNOWN
    provider_type: str = ""
    signals: frozenset[QuestionSignal] = frozenset()
    description: str | None = None
    unsupported_reason: str | None = None
    provider_question_id: str = ""
    provider_page_id: str = ""
    jump_rules: list[JumpRule] | None = None
    display_conditions: list[DisplayCondition] | None = None
    controls_display_targets: list[DisplayCondition] | None = None
    logic_parse_status: str = LOGIC_PARSE_STATUS_UNKNOWN

    @model_validator(mode="before")
    @classmethod
    def _absorb_legacy_bool_fields(cls, data: object) -> object:
        """旧格式的 is_/has_ 布尔键折叠进 signals（extra=forbid，必须摘除原键）。"""
        if not isinstance(data, dict):
            return data
        merged = {
            key: value
            for key, value in data.items()
            if key not in _LEGACY_BOOL_KEYS and key != "signals"
        }
        raw_signals = data.get("signals") or ()
        if isinstance(raw_signals, (str, bytes)) or not hasattr(raw_signals, "__iter__"):
            raise ValueError(f"signals must be an iterable of signal names, got {raw_signals!r}")
        signals: set[QuestionSignal] = set()
        for value in cast(Iterable[object], raw_signals):
            if isinstance(value, QuestionSignal):
                signals.add(value)
            elif isinstance(value, str) and value in _SIGNAL_VALUES:
                signals.add(QuestionSignal(value))
            else:
                raise ValueError(f"unknown question signal: {value!r}")
        signals.update(signal for key, signal in _LEGACY_BOOL_KEYS.items() if data.get(key))
        merged["signals"] = frozenset(signals)
        return merged

    @field_serializer("signals")
    def _dump_signals(self, value: frozenset[QuestionSignal]) -> list[str]:
        return sorted(signal.value for signal in value)


class _QuestionMetaBase(SurveyQuestionMeta):
    display_num: int | None = None
    question_media: list[QuestionMedia] | None = None


class ChoiceQuestionMeta(_QuestionMetaBase):
    option_texts: list[str] | None = None
    forced_option_index: int | None = None
    forced_option_text: str | None = None
    fillable_options: list[int] | None = None
    required_fillable_options: list[int] | None = None
    attached_option_selects: list[AttachedOptionSelect] | None = None


class SingleChoiceQuestionMeta(ChoiceQuestionMeta):
    pass


class MultipleChoiceQuestionMeta(ChoiceQuestionMeta):
    multi_min_limit: int | None = None
    multi_max_limit: int | None = None


class MatrixQuestionMeta(_QuestionMetaBase):
    rows: int = 1
    row_texts: list[str] | None = None
    option_texts: list[str] | None = None


class RatingQuestionMeta(_QuestionMetaBase):
    rating_max: int = 0


class TextQuestionMeta(_QuestionMetaBase):
    text_inputs: int = 0
    text_input_labels: list[str] | None = None
    location_verify_type: str = ""


class SliderQuestionMeta(_QuestionMetaBase):
    slider_min: int | float | None = None
    slider_max: int | float | None = None
    slider_step: int | float | None = None


@dataclass(frozen=True)
class SurveyDefinition:
    provider: str
    title: str
    questions: list[SurveyQuestionMeta]


SurveyQuestionInput = SurveyQuestionMeta | Mapping[str, object]


def _filter_kwargs(cls: type[BaseConfigModel], kwargs: dict[str, object]) -> dict[str, Any]:
    valid_fields = set(cls.model_fields)
    return {k: v for k, v in kwargs.items() if k in valid_fields}


def _survey_question_input_to_dict(question: object) -> dict[str, object] | None:
    if isinstance(question, SurveyQuestionMeta):
        return survey_question_meta_to_dict(question)
    if isinstance(question, Mapping):
        return cast("dict[str, object]", dict(question))
    return None


def survey_question_meta_to_dict(question: SurveyQuestionMeta) -> dict[str, object]:
    return question.model_dump()


def _resolve_type_code(normalized: Mapping[str, object]) -> QuestionType:
    raw = str(normalized.get("type_code") or "unknown")
    return convert_wire_type_code(raw)


def _collect_signals(
    normalized: Mapping[str, object], type_code: QuestionType, attached_list: list[dict[str, object]]
) -> frozenset[QuestionSignal]:
    """从输入 dict（新旧键皆可）装配信号集合。

    LOCATION / ATTACHED_OPTION_SELECT 与旧实现的分支边界一致：
    前者只在文本家族题型上生效，后者只在选项题家族上生效，
    其余题型即使输入带旧键也不落信号。
    """
    text_family = type_code in _TEXT_FAMILY_TYPES
    choice_family = type_code not in _NON_CHOICE_FAMILY_TYPES
    candidates = (
        (QuestionSignal.REQUIRED, bool(normalized.get("required"))),
        (
            QuestionSignal.UNSUPPORTED,
            bool(normalized.get("unsupported")) and type_code != QuestionType.DESCRIPTION,
        ),
        (QuestionSignal.JUMP, _signal_hit(normalized, QuestionSignal.JUMP, "has_jump")),
        (
            QuestionSignal.DISPLAY_CONDITION,
            _signal_hit(normalized, QuestionSignal.DISPLAY_CONDITION, "has_display_condition"),
        ),
        (
            QuestionSignal.DEPENDENT_DISPLAY_LOGIC,
            _signal_hit(
                normalized, QuestionSignal.DEPENDENT_DISPLAY_LOGIC, "has_dependent_display_logic"
            ),
        ),
        (
            QuestionSignal.ATTACHED_OPTION_SELECT,
            choice_family
            and bool(normalized.get("has_attached_option_select") or attached_list),
        ),
        (
            QuestionSignal.LOCATION,
            text_family
            and (bool(normalized.get("is_location")) or type_code == QuestionType.LOCATION),
        ),
    )
    signals = {signal for signal, present in candidates if present}
    raw_signals = normalized.get("signals")
    if isinstance(raw_signals, (list, tuple, set, frozenset)):
        for value in cast(Iterable[object], raw_signals):
            if isinstance(value, QuestionSignal):
                signals.add(value)
            elif isinstance(value, str) and value in _SIGNAL_VALUES:
                signals.add(QuestionSignal(value))
            else:
                raise ValueError(f"unknown question signal: {value!r}")
    elif raw_signals:
        raise ValueError(f"signals must be an iterable of signal names, got {raw_signals!r}")
    return frozenset(signals)


def _build_common_kwargs(
    normalized: dict[str, object], type_code: QuestionType, question_number: int
) -> dict[str, object]:
    unsupported_reason = normalized.get("unsupported_reason") or ""
    if _signal_hit(normalized, QuestionSignal.UNSUPPORTED, "unsupported") and not unsupported_reason:
        unsupported_reason = "当前平台暂不支持该题型"
    page_raw = normalized.get("page")
    try:
        page_number = max(1, int(page_raw)) if isinstance(page_raw, (int, float, str)) else 1
    except (ValueError, TypeError):
        page_number = 1
    return {
        "num": question_number,
        "title": normalized.get("title") or "",
        "type_code": type_code,
        "provider_type": normalized.get("type_code") or "",
        "description": normalized.get("description") or "" or None,
        "unsupported_reason": unsupported_reason or None,
        "provider_question_id": str(
            normalized.get("provider_question_id") or question_number
        ),
        "provider_page_id": str(normalized.get("provider_page_id") or page_number),
    }


def _build_logic_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    raw_display_num = normalized.get("display_num")
    display_number: int | None = None
    if raw_display_num not in (None, ""):
        try:
            display_number = int(cast("int | str", raw_display_num))
        except (ValueError, TypeError):
            display_number = None
    return {
        "display_num": display_number,
        "jump_rules": _normalize_jump_rules(normalized.get("jump_rules")) or None,
        "display_conditions": _normalize_dict_list(normalized.get("display_conditions")) or None,
        "controls_display_targets": _normalize_dict_list(normalized.get("controls_display_targets"))
        or None,
        "logic_parse_status": _infer_logic_parse_status(normalized),
        "question_media": _normalize_question_media_list(normalized.get("question_media")) or None,
    }


def _build_choice_kwargs(
    normalized: dict[str, object], attached_list: list[dict[str, object]]
) -> dict[str, object]:
    option_texts = _normalize_text_list(normalized.get("option_texts"))
    forced_option_index = normalized.get("forced_option_index")
    try:
        if forced_option_index is not None:
            forced_option_index = int(cast("int | str", forced_option_index))
    except (ValueError, TypeError):
        forced_option_index = None
    fillable_options_raw = normalized.get("fillable_options")
    fillable_options: list[int] = []
    if isinstance(fillable_options_raw, list):
        for raw in fillable_options_raw:
            try:
                fillable_options.append(int(cast("int | str", raw)))
            except (ValueError, TypeError):
                continue
    required_fillable_options_raw = normalized.get("required_fillable_options")
    required_fillable_options: list[int] = []
    if isinstance(required_fillable_options_raw, list):
        for raw in required_fillable_options_raw:
            try:
                required_fillable_options.append(int(cast("int | str", raw)))
            except (ValueError, TypeError):
                continue
    return {
        "option_texts": option_texts or None,
        "forced_option_index": forced_option_index,
        "forced_option_text": normalized.get("forced_option_text") or "" or None,
        "fillable_options": fillable_options or None,
        "required_fillable_options": required_fillable_options or None,
        "attached_option_selects": attached_list or None,
    }


def _build_matrix_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    row_texts = _normalize_text_list(normalized.get("row_texts"))
    rows_raw = normalized.get("rows")
    try:
        rows = (
            max(1, int(rows_raw))
            if isinstance(rows_raw, (int, float, str))
            else (len(row_texts) or 1)
        )
    except (ValueError, TypeError):
        rows = len(row_texts) or 1
    return {
        "rows": rows,
        "row_texts": row_texts or None,
        "option_texts": _normalize_text_list(normalized.get("option_texts")) or None,
    }


def _build_rating_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    options_raw = normalized.get("options")
    option_texts = _normalize_text_list(normalized.get("option_texts"))
    try:
        option_count = (
            max(0, int(options_raw))
            if isinstance(options_raw, (int, float, str))
            else len(option_texts)
        )
    except (ValueError, TypeError):
        option_count = len(option_texts)
    rating_max_raw = normalized.get("rating_max")
    try:
        rating_max = (
            max(0, int(rating_max_raw))
            if isinstance(rating_max_raw, (int, float, str))
            else option_count
        )
    except (ValueError, TypeError):
        rating_max = option_count
    return {
        "rating_max": rating_max,
    }


def _build_text_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    text_input_labels = _normalize_text_list(normalized.get("text_input_labels")) or None
    text_inputs_raw = normalized.get("text_inputs")
    try:
        text_inputs = (
            max(0, int(text_inputs_raw)) if isinstance(text_inputs_raw, (int, float, str)) else 0
        )
    except (ValueError, TypeError):
        text_inputs = 0
    if text_inputs == 0 and text_input_labels:
        text_inputs = len(text_input_labels)
    if text_inputs == 0 and bool(normalized.get("is_multi_text")):
        text_inputs = max(2, text_inputs)
    return {
        "text_inputs": text_inputs,
        "text_input_labels": text_input_labels,
        "location_verify_type": normalized.get("location_verify_type") or "",
    }


def _build_slider_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    return {
        "slider_min": normalized.get("slider_min"),
        "slider_max": normalized.get("slider_max"),
        "slider_step": normalized.get("slider_step"),
    }


def _normalize_question(
    question: SurveyQuestionInput, provider: str, index: int
) -> SurveyQuestionMeta:
    normalized = dict(_survey_question_input_to_dict(question) or {})
    num_raw = normalized.get("num")
    try:
        question_number = max(1, int(num_raw)) if isinstance(num_raw, (int, float, str)) else index
    except (ValueError, TypeError):
        question_number = index
    type_code = _resolve_type_code(normalized)

    attached_raw = normalized.get("attached_option_selects")
    attached_list = (
        _filter_attached_items(_normalize_dict_list(attached_raw))
        if isinstance(attached_raw, list)
        else []
    )
    common = _build_common_kwargs(normalized, type_code, question_number)
    logic = _build_logic_kwargs(normalized)
    signals = _collect_signals(normalized, type_code, attached_list)
    common["signals"] = signals

    match type_code:
        case QuestionType.SINGLE:
            return SingleChoiceQuestionMeta(
                **_filter_kwargs(
                    SingleChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized, attached_list)},
                )
            )
        case QuestionType.MULTIPLE:
            kwargs = {**common, **logic, **_build_choice_kwargs(normalized, attached_list)}
            kwargs["multi_min_limit"] = normalized.get("multi_min_limit")
            kwargs["multi_max_limit"] = normalized.get("multi_max_limit")
            return MultipleChoiceQuestionMeta(**_filter_kwargs(MultipleChoiceQuestionMeta, kwargs))
        case QuestionType.DROPDOWN | QuestionType.ORDER:
            return SingleChoiceQuestionMeta(
                **_filter_kwargs(
                    SingleChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized, attached_list)},
                )
            )
        case QuestionType.MATRIX:
            return MatrixQuestionMeta(
                **_filter_kwargs(
                    MatrixQuestionMeta,
                    {**common, **logic, **_build_matrix_kwargs(normalized)},
                )
            )
        case QuestionType.SCORE | QuestionType.SCALE:
            return RatingQuestionMeta(
                **_filter_kwargs(
                    RatingQuestionMeta,
                    {**common, **logic, **_build_rating_kwargs(normalized)},
                )
            )
        case QuestionType.SLIDER:
            return SliderQuestionMeta(
                **_filter_kwargs(
                    SliderQuestionMeta,
                    {**common, **logic, **_build_slider_kwargs(normalized)},
                )
            )
        case QuestionType.TEXT | QuestionType.MULTI_TEXT | QuestionType.LOCATION:
            return TextQuestionMeta(
                **_filter_kwargs(
                    TextQuestionMeta,
                    {**common, **logic, **_build_text_kwargs(normalized)},
                )
            )
        case QuestionType.DESCRIPTION:
            return _QuestionMetaBase(**_filter_kwargs(_QuestionMetaBase, {**common, **logic}))
        case _:
            return ChoiceQuestionMeta(
                **_filter_kwargs(
                    ChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized, attached_list)},
                )
            )


def ensure_survey_question_meta(
    question: SurveyQuestionInput,
    *,
    default_provider: str = SURVEY_PROVIDER_WJX,
    index: int = 1,
) -> SurveyQuestionMeta:
    return _normalize_question(
        question, normalize_survey_provider(default_provider, default=SURVEY_PROVIDER_WJX), index
    )


def ensure_survey_question_metas(
    questions: Iterable[SurveyQuestionInput],
    *,
    default_provider: str = SURVEY_PROVIDER_WJX,
) -> list[SurveyQuestionMeta]:
    normalized_provider = normalize_survey_provider(default_provider, default=SURVEY_PROVIDER_WJX)
    normalized: list[SurveyQuestionMeta] = []
    for index, question in enumerate(questions or [], start=1):
        if not isinstance(question, (SurveyQuestionMeta, Mapping)):
            continue
        normalized.append(_normalize_question(question, normalized_provider, index))
    return normalized


def serialize_survey_question_metas(
    questions: Iterable[SurveyQuestionInput],
) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for question in questions or []:
        normalized = _survey_question_input_to_dict(question)
        if normalized is not None:
            serialized.append(normalized)
    return serialized


def clone_survey_question_metas(
    questions: Iterable[SurveyQuestionInput],
    *,
    default_provider: str = SURVEY_PROVIDER_WJX,
) -> list[SurveyQuestionMeta]:
    serialized = serialize_survey_question_metas(questions)
    return ensure_survey_question_metas(serialized, default_provider=default_provider)


def normalize_survey_questions(
    provider: str, questions: Iterable[SurveyQuestionInput]
) -> list[SurveyQuestionMeta]:
    normalized_provider = normalize_survey_provider(provider, default=SURVEY_PROVIDER_WJX)
    return ensure_survey_question_metas(questions, default_provider=normalized_provider)


def build_survey_definition(
    provider: str, title: str, questions: Iterable[SurveyQuestionInput]
) -> SurveyDefinition:
    normalized_provider = normalize_survey_provider(provider, default=SURVEY_PROVIDER_WJX)
    return SurveyDefinition(
        provider=normalized_provider,
        title=str(title or "").strip(),
        questions=normalize_survey_questions(normalized_provider, questions),
    )
