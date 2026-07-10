from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.types import QuestionType
from survey_submitter.logging.log_utils import log_suppressed_exception

# Default number of options for rating-type questions (scale / score)
DEFAULT_RATING_OPTION_COUNT = 5

_TEXT_RANDOM_NAME_TOKEN = "__RANDOM_NAME__"
_TEXT_RANDOM_MOBILE_TOKEN = "__RANDOM_MOBILE__"
_TEXT_RANDOM_ID_CARD_TOKEN = "__RANDOM_ID_CARD__"
_TEXT_RANDOM_NONE = "none"
_TEXT_RANDOM_NAME = "name"
_TEXT_RANDOM_MOBILE = "mobile"
_TEXT_RANDOM_ID_CARD = "id_card"
_TEXT_RANDOM_INTEGER = "integer"
GLOBAL_RELIABILITY_DIMENSION = "__global_reliability__"

__all__ = [
    "DEFAULT_RATING_OPTION_COUNT",
    "GLOBAL_RELIABILITY_DIMENSION",
    "QuestionEntry",
    "_TEXT_RANDOM_ID_CARD",
    "_TEXT_RANDOM_ID_CARD_TOKEN",
    "_TEXT_RANDOM_INTEGER",
    "_TEXT_RANDOM_MOBILE",
    "_TEXT_RANDOM_MOBILE_TOKEN",
    "_TEXT_RANDOM_NAME",
    "_TEXT_RANDOM_NAME_TOKEN",
    "_TEXT_RANDOM_NONE",
    "_infer_option_count",
]


def _infer_option_count(entry: "QuestionEntry") -> int:
    

    def _nested_length(raw: Any) -> int | None:
        if not isinstance(raw, list):
            return None
        lengths: list[int] = []
        for item in raw:
            if isinstance(item, (list, tuple)):
                lengths.append(len(item))
        return max(lengths) if lengths else None

    if entry.question_type == QuestionType.MATRIX:
        nested_len = _nested_length(entry.custom_weights)
        if nested_len:
            return nested_len
        nested_len = _nested_length(entry.probabilities)
        if nested_len:
            return nested_len

    try:
        if entry.option_count and entry.option_count > 0:
            return int(entry.option_count)
    except Exception as exc:
        log_suppressed_exception("questions.schema._infer_option_count option_count", exc)
    try:
        if entry.custom_weights and len(entry.custom_weights) > 0:
            return len(entry.custom_weights)
    except Exception as exc:
        log_suppressed_exception("questions.schema._infer_option_count custom_weights", exc)
    try:
        if isinstance(entry.probabilities, (list, tuple)) and len(entry.probabilities) > 0:
            return len(entry.probabilities)
    except Exception as exc:
        log_suppressed_exception("questions.schema._infer_option_count probabilities", exc)
    try:
        if entry.texts and len(entry.texts) > 0:
            return len(entry.texts)
    except Exception as exc:
        log_suppressed_exception("questions.schema._infer_option_count texts", exc)
    if entry.question_type in (QuestionType.SCALE, QuestionType.SCORE):
        return DEFAULT_RATING_OPTION_COUNT
    return 0


class QuestionEntry(BaseConfigModel):
    question_type: str
    probabilities: list[float] | list[list[float]] | int | None
    texts: list[str] | None = None
    rows: int = 1
    option_count: int = 0
    distribution_mode: str = "random"
    custom_weights: list[float] | list[list[float]] | None = None
    question_num: int | None = None
    question_title: str | None = None
    survey_provider: str = "wjx"
    provider_question_id: str | None = None
    provider_page_id: str | None = None
    ai_enabled: bool = False
    multi_text_blank_modes: list[str] = []
    multi_text_blank_ai_flags: list[bool] = []
    multi_text_blank_int_ranges: list[list[int]] = []
    text_random_mode: str = _TEXT_RANDOM_NONE
    text_random_int_range: list[int] = []
    option_fill_texts: list[str | None] | None = None
    fillable_option_indices: list[int] | None = None
    attached_option_selects: list[dict] = []
    is_location: bool = False
    location_parts: list[str] = []
    dimension: str | None = None
    psycho_bias: str | list[str] = "custom"
