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
    "MultiTextBlankConfig",
    "ProviderInfo",
    "QuestionDetail",
    "QuestionAnswerConfig",
    "ChoiceQuestionAnswerConfig",
    "TextQuestionAnswerConfig",
    "MultiTextQuestionAnswerConfig",
    "LocationQuestionAnswerConfig",
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
    "answer_config_type_for_question_type",
]


# ---------------------------------------------------------------------------
# Nested dataclasses – logical groupings of question attributes
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


# ---------------------------------------------------------------------------
# _infer_option_count
# ---------------------------------------------------------------------------


def _infer_option_count(
    question_type: str,
    *,
    custom_weights: list[float] | list[list[float]] | None = None,
    probabilities: list[float] | list[list[float]] | int | None = None,
    option_count: int = 0,
    texts: list[str] | None = None,
) -> int:
    """Infer the option count from available distribution metadata."""

    def _nested_length(raw: object) -> int | None:
        if not isinstance(raw, list):
            return None
        lengths: list[int] = []
        for item in raw:
            if isinstance(item, (list, tuple)):
                lengths.append(len(item))
        return max(lengths) if lengths else None

    if question_type == QuestionType.MATRIX:
        nested_len = _nested_length(custom_weights)
        if nested_len:
            return nested_len
        nested_len = _nested_length(probabilities)
        if nested_len:
            return nested_len

    if option_count and option_count > 0:
        return int(option_count)
    if custom_weights and len(custom_weights) > 0:
        return len(custom_weights)
    if isinstance(probabilities, (list, tuple)) and len(probabilities) > 0:
        return len(probabilities)
    if texts and len(texts) > 0:
        return len(texts)
    if question_type in (QuestionType.SCALE, QuestionType.SCORE):
        return DEFAULT_RATING_OPTION_COUNT
    return 0


# ---------------------------------------------------------------------------
# QuestionAnswerConfig hierarchy (discriminated on `question_type`)
# ---------------------------------------------------------------------------


class QuestionAnswerConfig(BaseConfigModel):
    """Per-question answering behavior – base class for all question types.

    Subclasses narrow the fields to only those relevant for a specific
    question type, ensuring serialization never emits irrelevant config.
    """

    ai_enabled: bool = False


class ChoiceQuestionAnswerConfig(QuestionAnswerConfig):
    """Choice-style questions (single / multiple / dropdown / order)."""

    option_fill_texts: list[str | None] | None = None
    fillable_option_indices: list[int] | None = None
    attached_option_selects: list[dict] = Field(default_factory=list)


class TextQuestionAnswerConfig(QuestionAnswerConfig):
    """Single-text questions."""

    text_random_mode: str = _TEXT_RANDOM_NONE
    text_random_int_range: list[int] = Field(default_factory=list)


class MultiTextQuestionAnswerConfig(QuestionAnswerConfig):
    """Multi-text (多项填空) questions."""

    multi_text_blank_modes: list[str] = Field(default_factory=list)
    multi_text_blank_ai_flags: list[bool] = Field(default_factory=list)
    multi_text_blank_int_ranges: list[list[int]] = Field(default_factory=list)


class LocationQuestionAnswerConfig(QuestionAnswerConfig):
    """Location questions."""

    location_parts: list[str] = Field(default_factory=list)


# Mapping from question_type string to the appropriate AnswerConfig subclass.
_ANSWER_CONFIG_BY_QUESTION_TYPE: dict[str, type[QuestionAnswerConfig]] = {
    str(QuestionType.SINGLE): ChoiceQuestionAnswerConfig,
    str(QuestionType.MULTIPLE): ChoiceQuestionAnswerConfig,
    str(QuestionType.DROPDOWN): ChoiceQuestionAnswerConfig,
    str(QuestionType.ORDER): ChoiceQuestionAnswerConfig,
    str(QuestionType.TEXT): TextQuestionAnswerConfig,
    str(QuestionType.MULTI_TEXT): MultiTextQuestionAnswerConfig,
    str(QuestionType.LOCATION): LocationQuestionAnswerConfig,
}


def answer_config_type_for_question_type(
    question_type: str | QuestionType,
    *,
    location_parts: list[str] | None = None,
) -> type[QuestionAnswerConfig]:
    """Return the concrete QuestionAnswerConfig subclass for a question type.

    When *location_parts* is non-empty, returns
    :class:`LocationQuestionAnswerConfig` regardless of the question type,
    because a text-typed question with location parts behaves as a location
    question.
    """
    if location_parts:
        return LocationQuestionAnswerConfig
    try:
        key = str(QuestionType(str(question_type)))
    except ValueError:
        return QuestionAnswerConfig
    return _ANSWER_CONFIG_BY_QUESTION_TYPE.get(key, QuestionAnswerConfig)


# ---------------------------------------------------------------------------
# QuestionDetail – replaces the old QuestionEntry hierarchy
# ---------------------------------------------------------------------------


class QuestionDetail(BaseConfigModel):
    """Runtime details for a single survey question.

    Combined from the former ``QuestionEntry`` base + subclass fields.
    Embedded inside :class:`QuestionInfo.details`.
    """

    # -- Provider mapping (survey_provider is read from SurveySection) -----
    provider_question_id: str | None = None
    provider_page_id: str | None = None

    # -- Distribution / probability config ----------------------------------
    probabilities: list[float] | list[list[float]] | int | None = None
    distribution_mode: str = "random"
    custom_weights: list[float] | list[list[float]] | None = None
    dimension: str | None = None

    # -- Per-question answering behavior ------------------------------------
    answer_config: QuestionAnswerConfig = Field(default_factory=QuestionAnswerConfig)

    @property
    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
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
