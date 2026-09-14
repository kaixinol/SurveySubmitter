from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, field_validator

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.reverse_fill import ReverseFillRuntimeState, ReverseFillSpec
from survey_submitter.core.task.distribution_state import DistributionRuntimeMixin
from survey_submitter.core.task.progress_state import ThreadProgressMixin, ThreadProgressState
from survey_submitter.core.task.proxy_state import ProxyLease, ProxyRuntimeMixin
from survey_submitter.core.task.reverse_fill_state import ReverseFillRuntimeMixin
from survey_submitter.providers.contracts import SurveyQuestionMeta


class ProxyRuntimeConfig(BaseConfigModel):
    enabled: bool = False
    source: str = "custom"
    reuse: bool = False

    @field_validator("source", mode="before")
    @classmethod
    def validate_proxy_source(cls, v: Any) -> str:
        text = str(v or "custom").lower()
        return text if text in ("custom", "local") else "custom"


class SurveyIdentityConfig(BaseConfigModel):
    """问卷身份标识：URL、标题与平台。"""

    url: str = ""
    title: str = ""
    provider: str = "wjx"


class AnswerProbabilityConfig(BaseConfigModel):
    """按题型索引的答案概率分布与滑块目标值。"""

    single_prob: list[list[float] | int | float | None] = []
    dropdown_prob: list[list[float] | int | float | None] = []
    multiple_prob: list[list[float]] = []
    matrix_prob: list[list[float] | int | float | None] = []
    scale_prob: list[list[float] | int | float | None] = []
    slider_targets: list[float] = []


class TextAnswerConfig(BaseConfigModel):
    """填空题答案语料与多行文本逐空配置。"""

    texts: list[list[str]] = []
    texts_prob: list[list[float]] = []
    text_entry_types: list[str] = []
    text_ai_flags: list[bool] = []
    text_titles: list[str] = []
    multi_text_blank_modes: list[list[str]] = []
    multi_text_blank_ai_flags: list[list[bool]] = []
    multi_text_blank_int_ranges: list[list[list[int]]] = []


class LocationAnswerConfig(BaseConfigModel):
    """地区题的省市县结构与随机取值池。"""

    location_parts: dict[int, list[str]] = {}
    location_random_value_pools: dict[int, list[str]] = {}


class ChoiceFillConfig(BaseConfigModel):
    """选项题的填空文案、关联下拉与选填跳过比例。"""

    single_option_fill_texts: list[list[str | None] | None] = []
    single_attached_option_selects: list[list[dict[str, Any]]] = []
    dropdown_option_fill_texts: list[list[str | None] | None] = []
    multiple_option_fill_texts: list[list[str | None] | None] = []
    optional_fill_skip_ratio: float = 0.0


class AnswerPolicyConfig(BaseConfigModel):
    """答案一致性规则（constraints + per_question 合并）与回填规格。"""

    rules: list[dict[str, Any]] = []
    reverse_fill_spec: ReverseFillSpec | None = None


class QuestionMappingConfig(BaseConfigModel):
    """题号/题键到配置索引、维度与元数据的映射。"""

    question_config_index_map: dict[int, tuple[str, int]] = {}
    provider_question_idx_map: dict[str, tuple[str, int]] = {}
    question_dimension_map: dict[int, str | None] = {}
    question_strict_ratio_map: dict[int, bool] = {}
    questions_metadata: dict[int, SurveyQuestionMeta] = {}
    provider_question_metadata_map: dict[str, SurveyQuestionMeta] = {}


class NetworkIdentityConfig(BaseConfigModel):
    """代理、UA 与风控暂停等网络身份配置。"""

    proxy: ProxyRuntimeConfig = Field(default_factory=ProxyRuntimeConfig)
    random_user_agent: bool = False
    user_agent_ratios: dict[str, int] = {"wechat": 33, "mobile": 33, "pc": 34}
    pause_on_aliyun_captcha: bool = True


class AIAnsweringConfig(BaseConfigModel):
    """AI 答题开关与系统提示词。"""

    system_prompt: str = ""
    answering: bool = True


class TestProfileConfig(BaseConfigModel):
    """测试档案的固定答案与轮换策略。"""

    profiles: list[dict[int, str]] = []
    random: bool = True


class ExecutionControlConfig(BaseConfigModel):
    """线程数、目标份数、失败策略与提交节奏。"""

    num_threads: int = 1
    target_num: int = 1
    fail_threshold: int = 5
    stop_on_fail: bool = True
    submit_interval_range_seconds: tuple[int, int] = (0, 0)
    answer_duration_range_seconds: tuple[int, int] = (0, 0)
    answer_datetime_window_ms: tuple[int, int] = (0, 0)


class ExecutionConfig(BaseConfigModel):
    """引擎运行时配置：11 个语义分节通过组合嵌套，每组单一职责。"""

    survey: SurveyIdentityConfig = Field(default_factory=SurveyIdentityConfig)
    answer_probs: AnswerProbabilityConfig = Field(default_factory=AnswerProbabilityConfig)
    text_answers: TextAnswerConfig = Field(default_factory=TextAnswerConfig)
    location_answers: LocationAnswerConfig = Field(default_factory=LocationAnswerConfig)
    choice_fill: ChoiceFillConfig = Field(default_factory=ChoiceFillConfig)
    answer_policy: AnswerPolicyConfig = Field(default_factory=AnswerPolicyConfig)
    question_maps: QuestionMappingConfig = Field(default_factory=QuestionMappingConfig)
    control: ExecutionControlConfig = Field(default_factory=ExecutionControlConfig)
    network: NetworkIdentityConfig = Field(default_factory=NetworkIdentityConfig)
    ai: AIAnsweringConfig = Field(default_factory=AIAnsweringConfig)
    test_profiles: TestProfileConfig = Field(default_factory=TestProfileConfig)


@dataclass
class ExecutionState(
    ThreadProgressMixin,
    ProxyRuntimeMixin,
    DistributionRuntimeMixin,
    ReverseFillRuntimeMixin,
):
    config: ExecutionConfig = field(default_factory=ExecutionConfig)

    success_count: int = 0
    consecutive_fail_count: int = 0
    proxy_unavailable_fail_count: int = 0
    terminal_stop_category: str = ""
    terminal_failure_reason: str = ""
    terminal_stop_message: str = ""
    thread_progress: dict[str, ThreadProgressState] = field(default_factory=dict)
    distribution_runtime_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending_by_thread: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)

    proxy_waiting_threads: int = 0
    proxy_in_use_by_thread: dict[str, ProxyLease] = field(default_factory=dict)
    successful_proxy_addresses: set[str] = field(default_factory=set)
    proxy_cooldowns_by_address: dict[str, float] = field(default_factory=dict)
    proxy_ip_pool: deque = field(default_factory=deque)
    current_profile_index: int = 0
    reverse_fill_runtime: ReverseFillRuntimeState | None = None

    stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    _aliyun_captcha_stop_triggered: bool = False
    _aliyun_captcha_stop_lock: threading.Lock = field(default_factory=threading.Lock)
    _aliyun_captcha_popup_shown: bool = False
    _target_reached_stop_triggered: bool = False
    _target_reached_stop_lock: threading.Lock = field(default_factory=threading.Lock)
    _terminal_stop_lock: threading.Lock = field(default_factory=threading.Lock)
    _runtime_condition: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _runtime_async_event: Any = field(default=None, init=False, repr=False)
    _runtime_async_event_loop: Any = field(default=None, init=False, repr=False)
    _runtime_change_seq: int = field(default=0, init=False, repr=False)

    def __setattr__(self, name: str, value: Any) -> None:

        if name in _EXECUTION_STATE_FIELD_NAMES:
            object.__setattr__(self, name, value)
            return
        if name in _EXECUTION_CONFIG_FIELD_NAMES:
            group = _EXECUTION_CONFIG_FIELD_GROUPS.get(name, name)
            raise AttributeError(
                f"ExecutionState 不允许直接设置配置字段 '{name}'，"
                f"请改用 state.config.{group}.{name}"
            )
        object.__setattr__(self, name, value)

    def mark_terminal_stop(
        self,
        category: str,
        *,
        failure_reason: str = "",
        message: str = "",
        overwrite: bool = False,
    ) -> None:
        normalized_category = category or ""
        if not normalized_category:
            return
        normalized_failure_reason = failure_reason or ""
        normalized_message = message or ""
        with self._terminal_stop_lock:
            if self.terminal_stop_category and not overwrite:
                return
            self.terminal_stop_category = normalized_category
            self.terminal_failure_reason = normalized_failure_reason
            self.terminal_stop_message = normalized_message
        self.notify_runtime_change()

    def get_terminal_stop_snapshot(self) -> tuple[str, str, str]:
        with self._terminal_stop_lock:
            return (
                str(self.terminal_stop_category or ""),
                str(self.terminal_failure_reason or ""),
                str(self.terminal_stop_message or ""),
            )


def _collect_config_field_groups(model_cls: type[BaseConfigModel]) -> dict[str, str]:
    """字段名 → 所属组名（含顶层组名自身）的映射，用于拦截错误拼写。"""
    groups: dict[str, str] = {}
    for group_name, group_field in model_cls.model_fields.items():
        groups.setdefault(group_name, group_name)
        group_type = group_field.annotation
        if isinstance(group_type, type) and issubclass(group_type, BaseConfigModel):
            for field_name in group_type.model_fields:
                groups.setdefault(field_name, group_name)
    return groups


_EXECUTION_CONFIG_FIELD_GROUPS = _collect_config_field_groups(ExecutionConfig)
_EXECUTION_CONFIG_FIELD_NAMES = frozenset(_EXECUTION_CONFIG_FIELD_GROUPS)
_EXECUTION_STATE_FIELD_NAMES = frozenset(ExecutionState.__dataclass_fields__.keys())
