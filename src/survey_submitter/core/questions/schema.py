from __future__ import annotations

from dataclasses import dataclass, field

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
    """Probability distribution and psychometric dimension settings."""

    probabilities: list[float] | list[list[float]] | int | None = None
    distribution_mode: str = "random"
    custom_weights: list[float] | list[list[float]] | None = None
    dimension: str | None = None
    psycho_bias: str | list[str] = "custom"


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
# QuestionEntry
# ---------------------------------------------------------------------------


class QuestionEntry(BaseConfigModel):
    """Configuration for a single survey question.

    Attributes are organized into six logical groups.  Each group can also be
    accessed as a nested dataclass via the corresponding ``@property``:

    * **Provider** – ``provider_info`` → :class:`ProviderInfo`
    * **Distribution** – ``distribution_config`` → :class:`DistributionConfig`
    * **Multi-text blanks** – ``multi_text_blank_config`` → :class:`MultiTextBlankConfig`
    * **Text random** – ``text_random_config`` → :class:`TextRandomConfig`
    * **Fill options** – ``fill_options_config`` → :class:`FillOptionsConfig`
    * **Location** – ``location_info`` → :class:`LocationInfo`
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
    psycho_bias: str | list[str] = "custom"

    # -- Core question structure --------------------------------------------
    texts: list[str] | None = None
    rows: int = 1
    option_count: int = 0
    ai_enabled: bool = False

    # -- Multi-text blank handling ------------------------------------------
    multi_text_blank_modes: list[str] = []
    multi_text_blank_ai_flags: list[bool] = []
    multi_text_blank_int_ranges: list[list[int]] = []

    # -- Text random config -------------------------------------------------
    text_random_mode: str = _TEXT_RANDOM_NONE
    text_random_int_range: list[int] = []

    # -- Fill options -------------------------------------------------------
    option_fill_texts: list[str | None] | None = None
    fillable_option_indices: list[int] | None = None
    attached_option_selects: list[dict] = []

    # -- Location info ------------------------------------------------------
    is_location: bool = False
    location_parts: list[str] = []

    # -- Nested-group property accessors ------------------------------------

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
            psycho_bias=self.psycho_bias,
        )

    @property
    def multi_text_blank_config(self) -> MultiTextBlankConfig:
        return MultiTextBlankConfig(
            modes=list(self.multi_text_blank_modes),
            ai_flags=list(self.multi_text_blank_ai_flags),
            int_ranges=list(self.multi_text_blank_int_ranges),
        )

    @property
    def text_random_config(self) -> TextRandomConfig:
        return TextRandomConfig(
            mode=self.text_random_mode,
            int_range=list(self.text_random_int_range),
        )

    @property
    def fill_options_config(self) -> FillOptionsConfig:
        return FillOptionsConfig(
            option_fill_texts=self.option_fill_texts,
            fillable_option_indices=self.fillable_option_indices,
            attached_option_selects=list(self.attached_option_selects or []),
        )

    @property
    def location_info(self) -> LocationInfo:
        return LocationInfo(
            is_location=self.is_location,
            location_parts=list(self.location_parts),
        )
