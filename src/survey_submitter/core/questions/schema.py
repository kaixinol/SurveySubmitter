from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.types import QuestionType

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
    "DistributionConfig",
    "FillOptionsConfig",
    "LocationInfo",
    "MultiTextBlankConfig",
    "ProviderInfo",
    "QuestionEntry",
    "ChoiceQuestionEntry",
    "TextQuestionEntry",
    "MultiTextQuestionEntry",
    "LocationQuestionEntry",
    "TextRandomConfig",
    "_TEXT_RANDOM_ID_CARD",
    "_TEXT_RANDOM_ID_CARD_TOKEN",
    "_TEXT_RANDOM_INTEGER",
    "_TEXT_RANDOM_MOBILE",
    "_TEXT_RANDOM_MOBILE_TOKEN",
    "_TEXT_RANDOM_NAME",
    "_TEXT_RANDOM_NAME_TOKEN",
    "_TEXT_RANDOM_NONE",
    "_infer_option_count",
    "entry_type_for_question_type",
]


# ---------------------------------------------------------------------------
# Nested dataclasses – logical groupings of QuestionEntry attributes
# ---------------------------------------------------------------------------


@dataclass
class ProviderInfo:
    """Survey provider identity and question mapping."""

    survey_provider: str = "wjx"
    provider_question_id: str | None = None
    provider_page_id: str | None = None


@dataclass
class DistributionConfig:
    """Probability distribution settings."""

    probabilities: list[float] | list[list[float]] | int | None = None
    distribution_mode: str = "random"
    custom_weights: list[float] | list[list[float]] | None = None
    dimension: str | None = None


@dataclass
class MultiTextBlankConfig:
    """Per-blank handling for multi-text (多项填空) questions."""

    modes: list[str] = field(default_factory=list)
    ai_flags: list[bool] = field(default_factory=list)
    int_ranges: list[list[int]] = field(default_factory=list)


@dataclass
class TextRandomConfig:
    """Random-token generation settings for single-text questions."""

    mode: str = _TEXT_RANDOM_NONE
    int_range: list[int] = field(default_factory=list)


@dataclass
class FillOptionsConfig:
    """Fillable-option and attached-option-select configuration."""

    option_fill_texts: list[str | None] | None = None
    fillable_option_indices: list[int] | None = None
    attached_option_selects: list[dict] = field(default_factory=list)


@dataclass
class LocationInfo:
    """Location-question identity and sub-part breakdown."""

    is_location: bool = False
    location_parts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# _infer_option_count
# ---------------------------------------------------------------------------


def _infer_option_count(entry: "QuestionEntry") -> int:

    def _nested_length(raw: object) -> int | None:
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

    if entry.option_count and entry.option_count > 0:
        return int(entry.option_count)
    if entry.custom_weights and len(entry.custom_weights) > 0:
        return len(entry.custom_weights)
    if isinstance(entry.probabilities, (list, tuple)) and len(entry.probabilities) > 0:
        return len(entry.probabilities)
    if entry.texts and len(entry.texts) > 0:
        return len(entry.texts)
    if entry.question_type in (QuestionType.SCALE, QuestionType.SCORE):
        return DEFAULT_RATING_OPTION_COUNT
    return 0


# ---------------------------------------------------------------------------
# QuestionEntry hierarchy (discriminated on `question_type`)
# ---------------------------------------------------------------------------


class QuestionEntry(BaseConfigModel):
    """Configuration for a single survey question.

    Base class holds only the fields common to every question type. Type-specific
    fields live on the narrow subclasses (see ``entry_type_for_question_type``):

    * :class:`ChoiceQuestionEntry` – single / multiple / dropdown / order
    * :class:`TextQuestionEntry` – text
    * :class:`MultiTextQuestionEntry` – multi-text
    * :class:`LocationQuestionEntry` – location
    """

    # -- Identity -----------------------------------------------------------
    question_type: str
    question_num: int | None = None
    question_title: str | None = None

    # -- Provider -----------------------------------------------------------
    survey_provider: str = "wjx"
    provider_question_id: str | None = None
    provider_page_id: str | None = None

    # -- Distribution / probability config ----------------------------------
    probabilities: list[float] | list[list[float]] | int | None = None
    distribution_mode: str = "random"
    custom_weights: list[float] | list[list[float]] | None = None
    dimension: str | None = None

    # -- Core question structure --------------------------------------------
    texts: list[str] | None = None
    rows: int = 1
    option_count: int = 0
    ai_enabled: bool = False

    # -- Location flag (orthogonal: a text-typed question may also be a location)
    is_location: bool = False
    location_parts: list[str] = Field(default_factory=list)

    @property
    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            survey_provider=self.survey_provider,
            provider_question_id=self.provider_question_id,
            provider_page_id=self.provider_page_id,
        )

    @property
    def distribution_config(self) -> DistributionConfig:
        return DistributionConfig(
            probabilities=self.probabilities,
            distribution_mode=self.distribution_mode,
            custom_weights=self.custom_weights,
            dimension=self.dimension,
        )


class ChoiceQuestionEntry(QuestionEntry):
    """Choice-style questions (single / multiple / dropdown / order)."""

    attached_option_selects: list[dict] = Field(default_factory=list)
    fillable_option_indices: list[int] | None = None
    option_fill_texts: list[str | None] | None = None

    @property
    def fill_options_config(self) -> FillOptionsConfig:
        return FillOptionsConfig(
            option_fill_texts=self.option_fill_texts,
            fillable_option_indices=self.fillable_option_indices,
            attached_option_selects=list(self.attached_option_selects or []),
        )


class TextQuestionEntry(QuestionEntry):
    """Single-text questions."""

    text_random_mode: str = _TEXT_RANDOM_NONE
    text_random_int_range: list[int] = Field(default_factory=list)

    @property
    def text_random_config(self) -> TextRandomConfig:
        return TextRandomConfig(
            mode=self.text_random_mode,
            int_range=list(self.text_random_int_range),
        )


class MultiTextQuestionEntry(QuestionEntry):
    """Multi-text (多项填空) questions."""

    multi_text_blank_modes: list[str] = Field(default_factory=list)
    multi_text_blank_ai_flags: list[bool] = Field(default_factory=list)
    multi_text_blank_int_ranges: list[list[int]] = Field(default_factory=list)

    @property
    def multi_text_blank_config(self) -> MultiTextBlankConfig:
        return MultiTextBlankConfig(
            modes=list(self.multi_text_blank_modes),
            ai_flags=list(self.multi_text_blank_ai_flags),
            int_ranges=list(self.multi_text_blank_int_ranges),
        )


class LocationQuestionEntry(QuestionEntry):
    """Location questions."""

    @property
    def location_info(self) -> LocationInfo:
        return LocationInfo(
            is_location=self.is_location,
            location_parts=list(self.location_parts),
        )


# Question types that map to each narrow subclass. Any type not listed uses the
# base QuestionEntry directly. Keys are the string values of QuestionType.
_ENTRY_TYPE_BY_QUESTION_TYPE: dict[str, type[QuestionEntry]] = {
    str(QuestionType.SINGLE): ChoiceQuestionEntry,
    str(QuestionType.MULTIPLE): ChoiceQuestionEntry,
    str(QuestionType.DROPDOWN): ChoiceQuestionEntry,
    str(QuestionType.ORDER): ChoiceQuestionEntry,
    str(QuestionType.TEXT): TextQuestionEntry,
    str(QuestionType.MULTI_TEXT): MultiTextQuestionEntry,
    str(QuestionType.LOCATION): LocationQuestionEntry,
}


def entry_type_for_question_type(question_type: str | QuestionType) -> type[QuestionEntry]:
    """Return the concrete QuestionEntry subclass for a given question type."""
    try:
        key = str(QuestionType(str(question_type)))
    except ValueError:
        return QuestionEntry
    return _ENTRY_TYPE_BY_QUESTION_TYPE.get(key, QuestionEntry)


def make_question_entry(**kwargs: Any) -> QuestionEntry:
    """Construct the appropriate QuestionEntry subclass for ``question_type``.

    Fields irrelevant to the resolved subclass are ignored, so callers may pass a
    full flat field set (e.g. parsed from legacy YAML) without worrying about the
    ``extra="forbid"`` policy of the concrete model.
    """
    qtype = kwargs.get("question_type")
    cls = entry_type_for_question_type(str(qtype)) if qtype is not None else QuestionEntry
    allowed = set(cls.model_fields.keys())
    filtered = {key: value for key, value in kwargs.items() if key in allowed}
    return cls(**filtered)
