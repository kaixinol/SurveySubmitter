from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.types import TypeCode, convert_wire_type_code
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX, normalize_survey_provider

type JumpRule = dict[str, str | int | bool]
type DisplayCondition = dict[str, str | list[str]]
type QuestionMedia = dict[str, str | int | None]
type AttachedOptionSelect = dict[str, str | list[str] | list[float]]

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


def _as_int(value: object, default: int, *, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (ValueError, TypeError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    return number


def _normalize_text_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item or "").strip() for item in raw]


def _normalize_dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, object]] = []
    for item in raw:
        normalized = _survey_question_input_to_dict(item)
        if normalized is not None:
            items.append(normalized)
    return items


def _normalize_jump_rules(raw: object) -> list[JumpRule]:
    rules = _normalize_dict_list(raw)
    normalized_rules: list[JumpRule] = []
    terminate_keywords = ("结束作答", "结束答题", "结束填写", "终止作答", "停止作答")
    for rule in rules:
        normalized_rule = dict(rule)
        if "terminates_survey" not in normalized_rule:
            option_text = str(normalized_rule.get("option_text") or "").strip()
            normalized_rule["terminates_survey"] = bool(
                option_text and any(keyword in option_text for keyword in terminate_keywords)
            )
        else:
            normalized_rule["terminates_survey"] = bool(normalized_rule.get("terminates_survey"))
        normalized_rules.append(normalized_rule)
    return normalized_rules


def _infer_logic_parse_status(normalized: Mapping[str, object]) -> str:
    if "logic_parse_status" in normalized:
        explicit = str(normalized.get("logic_parse_status") or "").strip().lower()
        if explicit in _VALID_LOGIC_PARSE_STATUSES:
            return explicit
        return LOGIC_PARSE_STATUS_UNKNOWN

    has_logic = bool(
        normalized.get("has_jump")
        or normalized.get("has_display_condition")
        or normalized.get("has_dependent_display_logic")
    )
    if not has_logic:
        return LOGIC_PARSE_STATUS_NONE

    has_parsed_logic = bool(
        _normalize_dict_list(normalized.get("jump_rules"))
        or _normalize_dict_list(normalized.get("display_conditions"))
        or _normalize_dict_list(normalized.get("controls_display_targets"))
    )
    return LOGIC_PARSE_STATUS_COMPLETE if has_parsed_logic else LOGIC_PARSE_STATUS_UNKNOWN


def _normalize_question_media_list(raw: object) -> list[QuestionMedia]:
    if not isinstance(raw, list):
        return []
    items: list[QuestionMedia] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind != "image":
            continue
        scope = str(item.get("scope") or "").strip().lower()
        if scope not in {"title", "option", "row"}:
            continue
        source_url = str(item.get("source_url") or "").strip()
        if not source_url:
            continue
        index = item.get("index")
        if scope == "title":
            normalized_index = None
        else:
            if index is None:
                continue
            try:
                normalized_index = int(index)
            except (ValueError, TypeError):
                continue
            if normalized_index < 0:
                continue
        label = str(item.get("label") or "").strip()
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


class SurveyQuestionMeta(BaseConfigModel):
    num: int
    title: str
    type_code: TypeCode = TypeCode.UNKNOWN
    provider_type: str = ""
    required: bool = False
    description: str | None = None
    unsupported: bool = False
    unsupported_reason: str | None = None
    provider_question_id: str = ""
    provider_page_id: str = ""
    has_jump: bool = False
    jump_rules: list[JumpRule] | None = None
    has_display_condition: bool = False
    display_conditions: list[DisplayCondition] | None = None
    has_dependent_display_logic: bool = False
    controls_display_targets: list[DisplayCondition] | None = None
    logic_parse_status: str = LOGIC_PARSE_STATUS_UNKNOWN


class _QuestionMetaBase(SurveyQuestionMeta):
    display_num: int | None = None
    question_media: list[QuestionMedia] | None = None


class ChoiceQuestionMeta(_QuestionMetaBase):
    option_texts: list[str] | None = None
    forced_option_index: int | None = None
    forced_option_text: str | None = None
    fillable_options: list[int] | None = None
    attached_option_selects: list[AttachedOptionSelect] | None = None
    has_attached_option_select: bool = False


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
    is_location: bool = False
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


def _filter_kwargs(cls: type[BaseConfigModel], kwargs: dict[str, object]) -> dict[str, object]:
    valid_fields = set(cls.model_fields)
    return {k: v for k, v in kwargs.items() if k in valid_fields}


def _survey_question_input_to_dict(question: object) -> dict[str, object] | None:
    if isinstance(question, SurveyQuestionMeta):
        return survey_question_meta_to_dict(question)
    if isinstance(question, Mapping):
        return dict(question)
    return None


def survey_question_meta_to_dict(question: SurveyQuestionMeta) -> dict[str, object]:
    return question.model_dump()


def _resolve_type_code(normalized: Mapping[str, object]) -> TypeCode:
    raw = str(normalized.get("type_code") or "unknown").strip()
    return convert_wire_type_code(raw)


def _build_common_kwargs(
    normalized: dict[str, object], type_code: TypeCode, question_number: int
) -> dict[str, object]:
    unsupported_reason = str(normalized.get("unsupported_reason") or "").strip()
    if bool(normalized.get("unsupported")) and not unsupported_reason:
        unsupported_reason = "当前平台暂不支持该题型"
    page_number = _as_int(normalized.get("page"), 1, minimum=1)
    return {
        "num": question_number,
        "title": str(normalized.get("title") or "").strip(),
        "type_code": type_code,
        "provider_type": str(normalized.get("type_code") or "").strip(),
        "required": bool(normalized.get("required")),
        "description": str(normalized.get("description") or "").strip() or None,
        "unsupported": bool(normalized.get("unsupported")) and type_code != TypeCode.DESCRIPTION,
        "unsupported_reason": unsupported_reason or None,
        "provider_question_id": str(
            normalized.get("provider_question_id") or question_number
        ).strip(),
        "provider_page_id": str(normalized.get("provider_page_id") or page_number).strip(),
    }


def _build_logic_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    raw_display_num = normalized.get("display_num")
    display_number: int | None = None
    if raw_display_num not in (None, ""):
        try:
            display_number = int(raw_display_num)
        except (ValueError, TypeError):
            display_number = None
    return {
        "display_num": display_number,
        "has_jump": bool(normalized.get("has_jump")),
        "jump_rules": _normalize_jump_rules(normalized.get("jump_rules")) or None,
        "has_display_condition": bool(normalized.get("has_display_condition")),
        "display_conditions": _normalize_dict_list(normalized.get("display_conditions")) or None,
        "has_dependent_display_logic": bool(normalized.get("has_dependent_display_logic")),
        "controls_display_targets": _normalize_dict_list(normalized.get("controls_display_targets"))
        or None,
        "logic_parse_status": _infer_logic_parse_status(normalized),
        "question_media": _normalize_question_media_list(normalized.get("question_media")) or None,
    }


def _build_choice_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    option_texts = _normalize_text_list(normalized.get("option_texts"))
    forced_option_index = normalized.get("forced_option_index")
    try:
        if forced_option_index is not None:
            forced_option_index = int(forced_option_index)
    except (ValueError, TypeError):
        forced_option_index = None
    fillable_options_raw = normalized.get("fillable_options")
    fillable_options: list[int] = []
    if isinstance(fillable_options_raw, list):
        for raw in fillable_options_raw:
            try:
                fillable_options.append(int(raw))
            except (ValueError, TypeError):
                continue
    attached = normalized.get("attached_option_selects")
    attached_list = _normalize_dict_list(attached) if isinstance(attached, list) else []
    return {
        "option_texts": option_texts or None,
        "forced_option_index": forced_option_index,
        "forced_option_text": str(normalized.get("forced_option_text") or "").strip() or None,
        "fillable_options": fillable_options or None,
        "attached_option_selects": attached_list or None,
        "has_attached_option_select": bool(
            normalized.get("has_attached_option_select") or attached_list
        ),
    }


def _build_matrix_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    row_texts = _normalize_text_list(normalized.get("row_texts"))
    return {
        "rows": _as_int(normalized.get("rows"), len(row_texts) or 1, minimum=1),
        "row_texts": row_texts or None,
        "option_texts": _normalize_text_list(normalized.get("option_texts")) or None,
    }


def _build_rating_kwargs(normalized: dict[str, object]) -> dict[str, object]:
    option_count = _as_int(
        normalized.get("options"),
        len(_normalize_text_list(normalized.get("option_texts"))),
        minimum=0,
    )
    return {
        "rating_max": _as_int(normalized.get("rating_max"), option_count, minimum=0),
    }


def _build_text_kwargs(
    normalized: dict[str, object], type_code: TypeCode = TypeCode.UNKNOWN
) -> dict[str, object]:
    text_input_labels = _normalize_text_list(normalized.get("text_input_labels")) or None
    text_inputs = _as_int(normalized.get("text_inputs"), 0, minimum=0)
    if text_inputs == 0 and text_input_labels:
        text_inputs = len(text_input_labels)
    if text_inputs == 0 and bool(normalized.get("is_multi_text")):
        text_inputs = max(2, text_inputs)
    return {
        "text_inputs": text_inputs,
        "text_input_labels": text_input_labels,
        "is_location": bool(normalized.get("is_location")) or type_code == TypeCode.LOCATION,
        "location_verify_type": str(normalized.get("location_verify_type") or "").strip(),
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
    question_number = _as_int(normalized.get("num"), index, minimum=1)
    type_code = _resolve_type_code(normalized)

    common = _build_common_kwargs(normalized, type_code, question_number)
    logic = _build_logic_kwargs(normalized)

    match type_code:
        case TypeCode.SINGLE:
            return SingleChoiceQuestionMeta(
                **_filter_kwargs(
                    SingleChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized)},
                )
            )
        case TypeCode.MULTIPLE:
            kwargs = {**common, **logic, **_build_choice_kwargs(normalized)}
            kwargs["multi_min_limit"] = normalized.get("multi_min_limit")
            kwargs["multi_max_limit"] = normalized.get("multi_max_limit")
            return MultipleChoiceQuestionMeta(**_filter_kwargs(MultipleChoiceQuestionMeta, kwargs))
        case TypeCode.DROPDOWN | TypeCode.ORDER:
            return SingleChoiceQuestionMeta(
                **_filter_kwargs(
                    SingleChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized)},
                )
            )
        case TypeCode.MATRIX:
            return MatrixQuestionMeta(
                **_filter_kwargs(
                    MatrixQuestionMeta,
                    {**common, **logic, **_build_matrix_kwargs(normalized)},
                )
            )
        case TypeCode.SCORE | TypeCode.SCALE:
            return RatingQuestionMeta(
                **_filter_kwargs(
                    RatingQuestionMeta,
                    {**common, **logic, **_build_rating_kwargs(normalized)},
                )
            )
        case TypeCode.SLIDER:
            return SliderQuestionMeta(
                **_filter_kwargs(
                    SliderQuestionMeta,
                    {**common, **logic, **_build_slider_kwargs(normalized)},
                )
            )
        case TypeCode.TEXT | TypeCode.MULTI_TEXT | TypeCode.LOCATION:
            return TextQuestionMeta(
                **_filter_kwargs(
                    TextQuestionMeta,
                    {**common, **logic, **_build_text_kwargs(normalized, type_code)},
                )
            )
        case TypeCode.DESCRIPTION:
            return _QuestionMetaBase(**_filter_kwargs(_QuestionMetaBase, {**common, **logic}))
        case _:
            return ChoiceQuestionMeta(
                **_filter_kwargs(
                    ChoiceQuestionMeta,
                    {**common, **logic, **_build_choice_kwargs(normalized)},
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
