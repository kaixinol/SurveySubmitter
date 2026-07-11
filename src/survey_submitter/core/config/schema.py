from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.schema import QuestionEntry
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX
from survey_submitter.providers.contracts import SurveyQuestionMeta


class SurveySection(BaseConfigModel):
    url: str = ""
    survey_title: str = ""
    survey_provider: str = SURVEY_PROVIDER_WJX


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

    @field_validator("submit_interval_range_seconds", "answer_duration_range_seconds")
    @classmethod
    def validate_tuple_range(cls, v: tuple[int, int]) -> tuple[int, int]:
        if len(v) != 2:
            raise ValueError(f"必须是包含2个元素的元组: {v}")
        if v[0] < 0 or v[1] < 0:
            raise ValueError(f"元组元素不能为负数: {v}")
        return v


class AnswerConfigSection(BaseConfigModel):
    question_entries: list[QuestionEntry] = []
    questions_info: list[SurveyQuestionMeta] | None = []
    answer_rules: list[dict[str, Any]] = []


class RuntimeConfig(BaseConfigModel):
    survey: SurveySection = Field(default_factory=SurveySection)
    execution: ExecutionSection = Field(default_factory=ExecutionSection)
    answer_config: AnswerConfigSection = Field(default_factory=AnswerConfigSection)
