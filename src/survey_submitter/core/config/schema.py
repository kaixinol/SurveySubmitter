from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from survey_submitter.core.config.answer_datetime_window import normalize_answer_datetime_window
from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.schema import QuestionAnswerConfig, QuestionDetail
from survey_submitter.core.reverse_fill.schema import (
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SCORE,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_FORMAT_WJX_TEXT,
)
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    normalize_survey_provider,
)


# Reverse fill format constants
_REVERSE_FILL_FORMATS = {
    REVERSE_FILL_FORMAT_AUTO,
    REVERSE_FILL_FORMAT_WJX_SEQUENCE,
    REVERSE_FILL_FORMAT_WJX_SCORE,
    REVERSE_FILL_FORMAT_WJX_TEXT,
}


_DEFAULT_UA_RATIOS = {"wechat": 33, "mobile": 33, "pc": 34}


def _normalize_user_agent_ratios(raw_ratios: Any) -> dict[str, int]:
    """Normalize user agent ratios from raw input."""
    if not isinstance(raw_ratios, dict):
        return dict(_DEFAULT_UA_RATIOS)

    ratios: dict[str, int] = {}
    for device_type in _DEFAULT_UA_RATIOS:
        value = raw_ratios.get(device_type)
        try:
            int_value = int(value) if value is not None else 0
        except (ValueError, TypeError):
            int_value = 0
        if int_value < 0 or int_value > 100:
            return dict(_DEFAULT_UA_RATIOS)
        ratios[device_type] = int_value

    if sum(ratios.values()) != 100:
        return dict(_DEFAULT_UA_RATIOS)
    return ratios


class SurveySection(BaseConfigModel):
    url: str = ""
    title: str = ""
    provider: str = SURVEY_PROVIDER_WJX

    @model_validator(mode="after")
    def auto_detect_provider(self) -> SurveySection:
        normalized = normalize_survey_provider(
            self.provider,
            default=detect_survey_provider(self.url),
        )
        if normalized != self.provider:
            object.__setattr__(self, "provider", normalized)
        return self


class AISection(BaseConfigModel):
    answering: bool = True
    api_key: str = ""
    base_url: str = ""
    api_protocol: str = "auto"
    model: str = ""
    system_prompt: str = ""


class ReverseFillSection(BaseConfigModel):
    enabled: bool = False
    source_path: str = ""
    format: str = "auto"
    start_row: int = Field(default=1, ge=1)
    threads: int = Field(default=1, ge=1)

    @field_validator("format", mode="before")
    @classmethod
    def validate_format(cls, v: Any) -> str:
        v = str(v or "auto").lower().strip()
        return v if v in _REVERSE_FILL_FORMATS else "auto"

    @field_validator("start_row", "threads", mode="before")
    @classmethod
    def coerce_non_negative_int(cls, v: Any) -> int:
        try:
            value = int(v)
            return max(1, value)
        except (ValueError, TypeError):
            return 1


class ExecutionSection(BaseConfigModel):
    target_num: int = Field(default=1, ge=1)
    num_threads: int = Field(default=1, ge=1, le=100)

    submit_interval_range_seconds: tuple[int, int] = (0, 0)
    answer_duration_range_seconds: tuple[int, int] = (60, 120)
    answer_datetime_window: tuple[str, str] = ("", "")

    random_proxy_ip: bool = False
    proxy_source: str = "default"
    custom_proxy_api: str = ""
    proxy_area_code: str | None = None

    random_user_agent: bool = False
    user_agent_ratios: dict[str, int] = {"wechat": 33, "mobile": 33, "pc": 34}

    stop_on_fail: bool = True
    pause_on_aliyun_captcha: bool = True
    reliability_mode: bool = True
    persona: bool = True

    ai: AISection = Field(default_factory=AISection)
    reverse_fill: ReverseFillSection = Field(default_factory=ReverseFillSection)

    @field_validator("target_num", "num_threads", mode="before")
    @classmethod
    def coerce_non_negative_int(cls, v: Any) -> int:
        try:
            value = int(v)
            return max(1, value)
        except (ValueError, TypeError):
            return 1

    @field_validator("submit_interval_range_seconds", mode="before")
    @classmethod
    def coerce_submit_interval(cls, v: Any) -> tuple[int, int]:
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                return (int(v[0]), int(v[1]))
            except (ValueError, TypeError):
                pass
        return (0, 0)

    @field_validator("answer_duration_range_seconds", mode="before")
    @classmethod
    def coerce_answer_duration(cls, v: Any) -> tuple[int, int]:
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                return (int(v[0]), int(v[1]))
            except (ValueError, TypeError):
                pass
        return (60, 120)

    @field_validator("answer_datetime_window", mode="before")
    @classmethod
    def coerce_datetime_window(cls, v: Any) -> tuple[str, str]:
        if isinstance(v, (list, tuple)):
            return normalize_answer_datetime_window([str(x) for x in v])
        return ("", "")

    @field_validator("proxy_source", mode="before")
    @classmethod
    def validate_proxy_source(cls, v: Any) -> str:
        v = str(v or "default").lower().strip()
        return v if v in ("default", "benefit", "custom") else "default"

    @field_validator("user_agent_ratios", mode="before")
    @classmethod
    def normalize_ratios(cls, v: Any) -> dict[str, int]:
        return _normalize_user_agent_ratios(v)


class QuestionInfo(BaseConfigModel):
    """极简题目消息，持久化在配置中，供后续网站读取并生成作答配置。

    仅保留识别题目所需的精简字段，与运行时完整元数据
    (:class:`survey_submitter.providers.contracts.SurveyQuestionMeta`) 区分开。
    """

    num: int
    title: str = ""
    question_type: str = ""
    options: list[str] = []
    required: bool = False
    details: QuestionDetail = Field(default_factory=QuestionDetail)


class AnswerRulesConfig(BaseConfigModel):
    """Global answer rules with nested constraints and per-question overrides."""

    constraints: list[dict[str, Any]] = []
    per_question: list[dict[str, Any]] = []


class TestProfile(BaseConfigModel):
    fixed_answers: dict[int, str] = {}


class AnswerConfigSection(BaseConfigModel):
    survey_questions: list[QuestionInfo] = []
    answer_rules: AnswerRulesConfig = Field(default_factory=AnswerRulesConfig)
    test_profiles: list[TestProfile] = []


class RuntimeConfig(BaseConfigModel):
    survey: SurveySection = Field(default_factory=SurveySection)
    execution: ExecutionSection = Field(default_factory=ExecutionSection)
    answer_config: AnswerConfigSection = Field(default_factory=AnswerConfigSection)
