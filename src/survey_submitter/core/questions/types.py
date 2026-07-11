from __future__ import annotations

from enum import StrEnum


class QuestionType(StrEnum):
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
    FILL_BLANK = "fill_blank"
    MULTI_FILL_BLANK = "multi_fill_blank"
    DESCRIPTION = "description"


class TypeCode(StrEnum):
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
    DESCRIPTION = "description"


CHOICE_TYPES = frozenset(
    {QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.SCALE, QuestionType.SCORE}
)
TEXT_TYPES = frozenset({QuestionType.TEXT, QuestionType.MULTI_TEXT})
RATING_TYPES = frozenset({QuestionType.SCALE, QuestionType.SCORE})
MATRIX_TYPES = frozenset({QuestionType.MATRIX})
CHOICE_LIKE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE, QuestionType.DROPDOWN})


def convert_wire_type_code(raw: str) -> TypeCode:
    """Convert numeric wire-format type code to semantic TypeCode."""
    match str(raw or "").strip():
        case "3":
            return TypeCode.SINGLE
        case "4":
            return TypeCode.MULTIPLE
        case "5":
            return TypeCode.SCORE
        case "6":
            return TypeCode.MATRIX
        case "7":
            return TypeCode.DROPDOWN
        case "8":
            return TypeCode.SLIDER
        case "9":
            return TypeCode.MATRIX
        case "11":
            return TypeCode.ORDER
        case "1":
            return TypeCode.TEXT
        case "2":
            return TypeCode.LOCATION
        case "description":
            return TypeCode.DESCRIPTION
        case "score":
            return TypeCode.SCORE
        case "scale":
            return TypeCode.SCALE
        case "multi_text":
            return TypeCode.MULTI_TEXT
        case _:
            try:
                return TypeCode(str(raw or "").strip())
            except ValueError:
                return TypeCode.UNKNOWN


__all__ = [
    "CHOICE_LIKE_TYPES",
    "CHOICE_TYPES",
    "MATRIX_TYPES",
    "QuestionType",
    "RATING_TYPES",
    "TEXT_TYPES",
    "TypeCode",
    "convert_wire_type_code",
]
