from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.questions.schema import QuestionEntry
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX
from survey_submitter.providers.contracts import SurveyQuestionMeta


class RuntimeConfig(BaseConfigModel):
    url: str = ""
    survey_title: str = ""
    survey_provider: str = SURVEY_PROVIDER_WJX
    target: int = Field(default=1, ge=1)
    threads: int = Field(default=1, ge=1, le=100)
    submit_interval: tuple[int, int] = (0, 0)
    answer_duration: tuple[int, int] = (60, 120)
    answer_datetime_window: tuple[str, str] = ("", "")
    random_ip_enabled: bool = False
    proxy_source: str = "default"
    custom_proxy_api: str = ""
    proxy_area_code: str | None = None
    random_ua_enabled: bool = False
    random_ua_ratios: dict[str, int] = {"wechat": 33, "mobile": 33, "pc": 34}
    fail_stop_enabled: bool = True
    pause_on_aliyun_captcha: bool = True
    reliability_mode_enabled: bool = True
    psycho_target_alpha: float = Field(default=0.85, ge=0, le=1)
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_api_protocol: str = "auto"
    ai_model: str = ""
    ai_system_prompt: str = ""
    reverse_fill_enabled: bool = False
    reverse_fill_source_path: str = ""
    reverse_fill_format: str = "auto"
    reverse_fill_start_row: int = Field(default=1, ge=1)
    reverse_fill_threads: int = Field(default=1, ge=1)
    answer_rules: list[dict[str, Any]] = []
    dimension_groups: list[str] = []
    question_entries: list[QuestionEntry] = []
    questions_info: list[SurveyQuestionMeta] | None = []

    @field_validator("submit_interval", "answer_duration")
    @classmethod
    def validate_tuple_range(cls, v: tuple[int, int]) -> tuple[int, int]:
        if len(v) != 2:
            raise ValueError(f"必须是包含2个元素的元组: {v}")
        if v[0] < 0 or v[1] < 0:
            raise ValueError(f"元组元素不能为负数: {v}")
        return v

