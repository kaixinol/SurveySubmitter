from __future__ import annotations

from enum import StrEnum


class QuestionType(StrEnum):
    UNKNOWN = "unknown"
    SINGLE = "single"
    MULTIPLE = "multiple"
    TEXT = "text"
    MULTI_TEXT = "multi_text"
    MATRIX = "matrix"
    SCALE = "scale"
    SCORE = "score"
    DROPDOWN = "dropdown"
    SLIDER = "slider"
    ORDER = "order"
    LOCATION = "location"
    UNIVERSITY = "university"
    FILL_BLANK = "fill_blank"
    MULTI_FILL_BLANK = "multi_fill_blank"
    DESCRIPTION = "description"


CHOICE_TYPES = frozenset(
    {QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.SCALE, QuestionType.SCORE}
)
TEXT_TYPES = frozenset({QuestionType.TEXT, QuestionType.MULTI_TEXT})
RATING_TYPES = frozenset({QuestionType.SCALE, QuestionType.SCORE})
MATRIX_TYPES = frozenset({QuestionType.MATRIX})
CHOICE_LIKE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE, QuestionType.DROPDOWN})


def convert_wire_type_code(raw: str) -> QuestionType:
    """Convert numeric wire-format type code to semantic QuestionType."""
    match raw or "":
        case "3":
            return QuestionType.SINGLE
        case "4":
            return QuestionType.MULTIPLE
        case "5":
            return QuestionType.SCORE
        case "6":
            return QuestionType.MATRIX
        case "7":
            return QuestionType.DROPDOWN
        case "8":
            return QuestionType.SLIDER
        case "9":
            return QuestionType.MATRIX
        case "11":
            return QuestionType.ORDER
        case "1":
            return QuestionType.TEXT
        case "2":
            return QuestionType.LOCATION
        case "description":
            return QuestionType.DESCRIPTION
        case "score":
            return QuestionType.SCORE
        case "scale":
            return QuestionType.SCALE
        case "multi_text":
            return QuestionType.MULTI_TEXT
        case _:
            try:
                return QuestionType(raw or "")
            except ValueError:
                return QuestionType.UNKNOWN


__all__ = [
    "CHOICE_LIKE_TYPES",
    "CHOICE_TYPES",
    "MATRIX_TYPES",
    "QuestionType",
    "RATING_TYPES",
    "TEXT_TYPES",
    "convert_wire_type_code",
]
