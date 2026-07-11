from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from survey_submitter.core.config.base import BaseConfigModel
from survey_submitter.core.reverse_fill import ReverseFillRuntimeState, ReverseFillSpec
from survey_submitter.core.task.distribution_state import DistributionRuntimeMixin
from survey_submitter.core.task.progress_state import ThreadProgressMixin, ThreadProgressState
from survey_submitter.core.task.proxy_state import ProxyLease, ProxyRuntimeMixin
from survey_submitter.core.task.reverse_fill_state import ReverseFillRuntimeMixin
from survey_submitter.providers.contracts import SurveyQuestionMeta


class ExecutionConfig(BaseConfigModel):
    url: str = ""
    survey_title: str = ""
    survey_provider: str = "wjx"

    single_prob: list[list[float] | int | float | None] = []
    droplist_prob: list[list[float] | int | float | None] = []
    multiple_prob: list[list[float]] = []
    matrix_prob: list[list[float] | int | float | None] = []
    scale_prob: list[list[float] | int | float | None] = []
    slider_targets: list[float] = []
    texts: list[list[str]] = []
    texts_prob: list[list[float]] = []
    text_entry_types: list[str] = []
    text_ai_flags: list[bool] = []
    text_titles: list[str] = []
    location_parts: dict[int, list[str]] = {}
    multi_text_blank_modes: list[list[str]] = []
    multi_text_blank_ai_flags: list[list[bool]] = []
    multi_text_blank_int_ranges: list[list[list[int]]] = []
    single_option_fill_texts: list[list[str | None] | None] = []
    single_attached_option_selects: list[list[dict[str, Any]]] = []
    droplist_option_fill_texts: list[list[str | None] | None] = []
    multiple_option_fill_texts: list[list[str | None] | None] = []
    answer_rules: list[dict[str, Any]] = []
    reverse_fill_spec: ReverseFillSpec | None = None

    question_config_index_map: dict[int, tuple[str, int]] = {}
    provider_question_config_index_map: dict[str, tuple[str, int]] = {}
    question_dimension_map: dict[int, str | None] = {}
    question_strict_ratio_map: dict[int, bool] = {}
    questions_metadata: dict[int, SurveyQuestionMeta] = {}
    provider_question_metadata_map: dict[str, SurveyQuestionMeta] = {}

    num_threads: int = 1
    target_num: int = 1
    fail_threshold: int = 5
    stop_on_fail_enabled: bool = True

    submit_interval_range_seconds: tuple[int, int] = (0, 0)
    answer_duration_range_seconds: tuple[int, int] = (0, 0)
    answer_datetime_window_ms: tuple[int, int] = (0, 0)

    random_proxy_ip_enabled: bool = False
    proxy_source: str = "default"
    proxy_ip_pool: Any = Field(default_factory=deque)
    random_user_agent_enabled: bool = False
    user_agent_ratios: dict[str, int] = {"wechat": 33, "mobile": 33, "pc": 34}
    pause_on_aliyun_captcha: bool = True
    ai_system_prompt: str = ""
    persona_enabled: bool = True
    ai_answering_enabled: bool = True


@dataclass
class ExecutionState(
    ThreadProgressMixin,
    ProxyRuntimeMixin,
    DistributionRuntimeMixin,
    ReverseFillRuntimeMixin,
):
    config: ExecutionConfig = field(default_factory=ExecutionConfig)

    cur_num: int = 0
    cur_fail: int = 0
    proxy_unavailable_fail_count: int = 0
    terminal_stop_category: str = ""
    terminal_failure_reason: str = ""
    terminal_stop_message: str = ""
    thread_progress: dict[str, ThreadProgressState] = field(default_factory=dict)
    distribution_runtime_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    distribution_pending_by_thread: dict[str, list[tuple[str, int, int]]] = field(
        default_factory=dict
    )

    proxy_waiting_threads: int = 0
    proxy_in_use_by_thread: dict[str, ProxyLease] = field(default_factory=dict)
    successful_proxy_addresses: set[str] = field(default_factory=set)
    proxy_cooldown_until_by_address: dict[str, float] = field(default_factory=dict)
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
            raise AttributeError(
                f"ExecutionState 不允许直接设置配置字段 '{name}'，请改用 state.config.{name}"
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
        normalized_category = str(category or "").strip()
        if not normalized_category:
            return
        normalized_failure_reason = str(failure_reason or "").strip()
        normalized_message = str(message or "").strip()
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


_EXECUTION_CONFIG_FIELD_NAMES = frozenset(ExecutionConfig.model_fields.keys())
_EXECUTION_STATE_FIELD_NAMES = frozenset(ExecutionState.__dataclass_fields__.keys())
