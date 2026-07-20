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
    FILL_BLANK = "fill_blank"
    MULTI_FILL_BLANK = "multi_fill_blank"
    DESCRIPTION = "description"


# ``TypeCode`` 是 ``QuestionType`` 的别名（``StrEnum`` 为 final 不可子类化，
# 故以别名方式表达「问卷平台原始题型码 / wire-provenance」语义层）。
# 两者成员与值完全一致，``TypeCode.X`` 与 ``QuestionType.X`` 等价。
TypeCode = QuestionType


CHOICE_TYPES = frozenset(
    {QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.SCALE, QuestionType.SCORE}
)
TEXT_TYPES = frozenset({QuestionType.TEXT, QuestionType.MULTI_TEXT})
RATING_TYPES = frozenset({QuestionType.SCALE, QuestionType.SCORE})
MATRIX_TYPES = frozenset({QuestionType.MATRIX})
CHOICE_LIKE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE, QuestionType.DROPDOWN})


def convert_wire_type_code(raw: str) -> QuestionType:
    """Convert numeric wire-format type code to semantic QuestionType."""
    match str(raw or "").strip():
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
                return QuestionType(str(raw or "").strip())
            except ValueError:
                return QuestionType.UNKNOWN


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
