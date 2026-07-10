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
    UNKNOWN = "0"
    GAPFILL = "1"
    LOCATION_TEXT = "2"
    RADIO = "3"
    CHECKBOX = "4"
    RATING = "5"
    MATRIX = "6"
    DROPDOWN = "7"
    SLIDER = "8"
    MATRIX_TEXT = "9"
    ORDER = "11"
    CAPTCHA = "33"
    SIGNATURE = "34"


# Common type groupings (replaces repeated inline tuples)
CHOICE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.DROPDOWN, QuestionType.SCALE, QuestionType.SCORE})
TEXT_TYPES = frozenset({QuestionType.TEXT, QuestionType.MULTI_TEXT})
RATING_TYPES = frozenset({QuestionType.SCALE, QuestionType.SCORE})
MATRIX_TYPES = frozenset({QuestionType.MATRIX})
CHOICE_LIKE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE, QuestionType.DROPDOWN})

__all__ = [
    "CHOICE_LIKE_TYPES",
    "CHOICE_TYPES",
    "MATRIX_TYPES",
    "QuestionType",
    "RATING_TYPES",
    "TEXT_TYPES",
    "TypeCode",
]
