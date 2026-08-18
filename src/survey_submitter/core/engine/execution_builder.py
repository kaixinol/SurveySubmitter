"""Execution artifact builder – pure core logic, no UI dependencies.

Migrated from the legacy GUI run controller.
This module prepares everything the async engine needs before a run:
validates the RuntimeConfig, resolves provider details, clones question
metadata, validates question configuration, optionally builds a reverse-fill
spec, and constructs the ExecutionConfig template with probabilities.
"""

from __future__ import annotations

import asyncio
import copy
import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger

from survey_submitter.core.config.answer_datetime_window import (
    answer_datetime_window_to_epoch_ms,
    normalize_answer_datetime_window,
    parse_answer_datetime_string,
)
from survey_submitter.core.config.schema import QuestionInfo, RuntimeConfig
from survey_submitter.core.questions.config import (
    configure_probabilities,
    validate_question_config,
)
from survey_submitter.core.reverse_fill import ReverseFillSpec
from survey_submitter.core.reverse_fill.validation import (
    build_enabled_reverse_fill_spec,
)
from survey_submitter.core.task import ExecutionConfig, ProxyRuntimeConfig
from survey_submitter.network.proxy import (
    get_custom_proxy_api_override,
    set_proxy_api_override,
    set_proxy_area_code,
    set_proxy_occupy_minute_by_answer_duration,
)
from survey_submitter.network.proxy.local_source import resolve_local_proxy_addresses
from survey_submitter.network.proxy.pool import (
    coerce_proxy_lease,
    is_proxy_responsive,
)
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
    detect_survey_provider,
    make_provider_question_key,
    normalize_survey_provider,
    supports_answer_datetime_window,
)
from survey_submitter.providers.contracts import SurveyQuestionMeta
from survey_submitter.providers.errors import (
    SurveyEnterpriseUnavailableError,
    SurveyStoppedError,
)
from survey_submitter.providers.wjx.parser import (
    ENTERPRISE_UNAVAILABLE_SURVEY_ERROR_MESSAGE,
    STOPPED_SURVEY_ERROR_MESSAGE,
    is_enterprise_unavailable_survey_page,
    is_stopped_survey_page,
)

# Maximum number of concurrent HTTP threads.
# Previously imported from survey_submitter.constants; defined here to keep
# this module free of application-layer dependencies.
HTTP_MAX_THREADS: int = 64


# ---------------------------------------------------------------------------
# Data classes & exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedExecutionArtifacts:
    """Immutable bundle produced by :func:`prepare_execution_artifacts`."""

    execution_config_template: ExecutionConfig
    proxy_ip_pool: deque
    provider: str
    survey_questions: list[QuestionInfo]
    questions_info: list[SurveyQuestionMeta]
    reverse_fill_spec: ReverseFillSpec | None


class RuntimePreparationError(Exception):
    """Raised when the runtime cannot be prepared from the given config."""

    def __init__(
        self,
        user_message: str,
        *,
        log_message: str = "",
        detailed: bool = False,
    ) -> None:
        super().__init__(str(user_message or "运行准备失败"))
        self.user_message = str(user_message or "运行准备失败")
        self.log_message = str(log_message or self.user_message)
        self.detailed = bool(detailed)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_thread_limit(config: RuntimeConfig) -> int:
    del config
    return HTTP_MAX_THREADS


def _resolve_survey_provider(config: RuntimeConfig) -> str:
    return normalize_survey_provider(
        config.survey.provider,
        default=detect_survey_provider(config.survey.url) or SURVEY_PROVIDER_WJX,
    )


def _resolve_survey_title(config: RuntimeConfig, fallback_title: str) -> str:
    config_title = str(config.survey.title or "")
    return config_title or str(fallback_title or "")


def _extract_answer_duration_for_proxy(config: RuntimeConfig) -> tuple[int, int]:
    raw = config.execution.answer_duration_range_seconds or (0, 0)
    return (int(raw[0]), int(raw[1]))


def _resolve_answer_datetime_window(config: RuntimeConfig) -> tuple[str, str]:
    return normalize_answer_datetime_window(config.execution.answer_datetime_window)


def _validate_datetime_window(config: RuntimeConfig, provider: str) -> None:
    if not supports_answer_datetime_window(provider):
        return
    start_text, end_text = _resolve_answer_datetime_window(config)
    if not start_text and not end_text:
        return
    if not start_text or not end_text:
        raise RuntimePreparationError("见数作答时间窗未配完整，请先设置开始和结束日期时间")
    start_dt = parse_answer_datetime_string(start_text)
    end_dt = parse_answer_datetime_string(end_text)
    if start_dt is None or end_dt is None:
        raise RuntimePreparationError("见数作答时间窗格式无效，请重新选择日期时间")
    if end_dt <= start_dt:
        raise RuntimePreparationError("见数结束日期时间必须晚于开始日期时间")
    max_duration_seconds = max(0, int((config.execution.answer_duration_range_seconds[1])))
    window_seconds = int((end_dt - start_dt).total_seconds())
    if window_seconds < max_duration_seconds:
        raise RuntimePreparationError("见数作答时间窗太窄，容不下当前最长作答时长")


def _verify_wjx_survey_is_answerable(config: RuntimeConfig, provider: str) -> None:
    if provider != SURVEY_PROVIDER_WJX:
        return
    url = str(config.survey.url or "").strip()
    if not url:
        return
    try:
        import survey_submitter.network.http as http_client
        from survey_submitter.constants import DEFAULT_HTTP_HEADERS

        response = http_client.get(url, timeout=8, headers=DEFAULT_HTTP_HEADERS, proxies={})
        response.raise_for_status()
    except (http_client.HTTPError, OSError, TimeoutError):
        logger.opt(exception=True).info("启动前问卷星状态复查失败，已放行到运行时处理")
        return
    html = str(getattr(response, "text", "") or "")
    if is_stopped_survey_page(html):
        raise SurveyStoppedError(STOPPED_SURVEY_ERROR_MESSAGE)
    if is_enterprise_unavailable_survey_page(html):
        raise SurveyEnterpriseUnavailableError(ENTERPRISE_UNAVAILABLE_SURVEY_ERROR_MESSAGE)


def _build_questions_metadata(
    questions_info: list[SurveyQuestionMeta],
) -> dict[int, SurveyQuestionMeta]:
    metadata: dict[int, SurveyQuestionMeta] = {}
    for item in questions_info:
        try:
            question_num = int(item.num or 0)
        except (ValueError, TypeError):
            question_num = 0
        if question_num > 0:
            metadata[question_num] = item
    return metadata


def _build_provider_metadata(
    questions_info: list[SurveyQuestionMeta],
    *,
    provider: str = "",
) -> dict[str, SurveyQuestionMeta]:
    metadata: dict[str, SurveyQuestionMeta] = {}
    for item in questions_info:
        provider_key = make_provider_question_key(
            provider,
            item.provider_page_id,
            item.provider_question_id,
        )
        if provider_key and provider_key not in metadata:
            metadata[provider_key] = item
    return metadata


def _sync_and_validate_random_proxy_config(config: RuntimeConfig) -> None:
    proxy = config.execution.proxy
    if not bool(proxy.enabled):
        return
    source = str(proxy.source or "custom").strip().lower()
    if source == "local":
        set_proxy_api_override(None)
        try:
            resolved = resolve_local_proxy_addresses(proxy.ip_list)
        except RuntimeError as exc:
            raise RuntimePreparationError(
                str(exc),
                log_message=f"本地代理列表解析失败：{exc}",
            ) from exc
        if not resolved:
            raise RuntimePreparationError(
                "已启用本地代理，但未配置静态代理列表，请先在设置中填写代理IP列表"
                "（支持本地文件路径或 http(s) 链接）",
                log_message="proxy.enabled 已开启且 source=local 但未配置 proxy.ip_list",
            )
        target_num = max(1, int(config.execution.target_num or 1))
        max_proxies = max(1, math.ceil(target_num * 1.5))
        num_threads = max(1, int(config.execution.num_threads or 1))
        # 并发探测数与提交并发数挂钩，设下限避免大列表时过慢
        max_workers = max(1, min(len(resolved), max(num_threads, 8)))
        passing: dict[str, None] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_address = {
                executor.submit(is_proxy_responsive, address): address
                for address in resolved
            }
            for future in as_completed(future_to_address):
                if len(passing) >= max_proxies:
                    for pending in future_to_address:
                        pending.cancel()
                    break
                address = future_to_address[future]
                if bool(future.result()):
                    passing[address] = None
                else:
                    logger.warning(f"本地代理未通过 wjx.cn 连通性测试，已丢弃：{address}")
        responsive = [address for address in resolved if address in passing][:max_proxies]
        if not responsive:
            raise RuntimePreparationError(
                "本地代理列表中的代理均无法连接 wjx.cn，请检查代理地址或网络连接",
                log_message="source=local 的静态代理列表全部未通过 wjx.cn 连通性测试",
            )
        if len(resolved) > max_proxies:
            logger.info(
                f"本地代理列表共解析 {len(resolved)} 个，已取通过连通性测试的"
                f" {len(responsive)} 个（上限 target_num*1.5={max_proxies}）"
            )
        proxy.ip_list = responsive
        return
    try:
        if str(proxy.custom_api_url or "").strip():
            set_proxy_api_override(proxy.custom_api_url)
        if str(proxy.area_code or "").strip():
            set_proxy_area_code(proxy.area_code)
    except ValueError as exc:
        raise RuntimePreparationError(
            str(exc),
            log_message=f"代理API地址校验失败：{exc}",
        ) from exc
    if not get_custom_proxy_api_override():
        raise RuntimePreparationError(
            "已开启随机IP，但未配置代理API地址，请先在设置中填写API地址",
            log_message="proxy.enabled 已开启但未配置自定义代理API地址",
        )


def _build_execution_config_template(
    config: RuntimeConfig,
    *,
    title: str,
    provider: str,
    reverse_fill_spec: ReverseFillSpec | None,
    questions_info: list[SurveyQuestionMeta],
) -> ExecutionConfig:
    thread_limit = _resolve_thread_limit(config)
    requested_target_num = max(1, int(config.execution.target_num or 1))
    requested_num_threads = max(1, int(config.execution.num_threads or 1))
    if reverse_fill_spec is not None:
        requested_target_num = max(1, int(reverse_fill_spec.target_num or 1))
        requested_num_threads = max(
            1,
            int(config.execution.reverse_fill.threads or config.execution.num_threads or 1),
        )
        requested_num_threads = min(requested_num_threads, requested_target_num)

    execution_config = ExecutionConfig(
        url=str(config.survey.url or ""),
        title=title,
        provider=provider,
        target_num=requested_target_num,
        num_threads=max(1, min(thread_limit, requested_num_threads)),
        fail_threshold=5,
        submit_interval_range_seconds=(
            int(config.execution.submit_interval_range_seconds[0]),
            int(config.execution.submit_interval_range_seconds[1]),
        ),
        answer_duration_range_seconds=(
            int(config.execution.answer_duration_range_seconds[0]),
            int(config.execution.answer_duration_range_seconds[1]),
        ),
        answer_datetime_window_ms=answer_datetime_window_to_epoch_ms(
            config.execution.answer_datetime_window
        ),
        proxy=ProxyRuntimeConfig(
            enabled=bool(config.execution.proxy.enabled),
            source=str(config.execution.proxy.source or "custom").strip().lower(),
            reuse=bool(config.execution.proxy.reuse),
        ),
        random_user_agent=bool(config.execution.random_user_agent),
        user_agent_ratios=copy.deepcopy(dict(config.execution.user_agent_ratios or {})),
        pause_on_aliyun_captcha=bool(config.execution.pause_on_aliyun_captcha),
        stop_on_fail=bool(config.execution.stop_on_fail),
        answer_rules=copy.deepcopy(
            list(config.answer_config.answer_rules.constraints or [])
            + list(config.answer_config.answer_rules.per_question or [])
        ),
        optional_fill_skip_ratio=min(
            1.0,
            max(0.0, float(config.answer_config.optional_fill_skip_ratio or 0.0)),
        ),
        reverse_fill_spec=copy.deepcopy(reverse_fill_spec),
        ai_system_prompt=str(config.execution.ai.system_prompt or "").strip(),
        ai_answering=bool(config.execution.ai.answering),
        test_profiles=[
            {int(k): str(v) for k, v in tp.fixed_answers.items()}
            for tp in config.answer_config.test_profiles.profiles
        ],
        test_profiles_random=bool(config.answer_config.test_profiles.random),
    )
    execution_config.questions_metadata = _build_questions_metadata(questions_info)
    execution_config.provider_question_metadata_map = _build_provider_metadata(
        questions_info,
        provider=str(config.survey.provider or ""),
    )
    return execution_config


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def prepare_execution_artifacts(
    config: RuntimeConfig,
    *,
    fallback_survey_title: str = "",
    questions_info: list[SurveyQuestionMeta] | None = None,
) -> PreparedExecutionArtifacts:
    """Validate *config* and build everything the engine needs to run.

    This is the GUI-agnostic equivalent of the former
    ``runtime_preparation.prepare_execution_artifacts``.

    ``questions_info`` is the parsed survey definition (question metadata).
    It is no longer persisted in the config; callers that already have the
    parsed definition (e.g. the CLI after fetching the survey) should pass it
    here. If omitted, it is re-parsed from ``config.survey.url``.
    """
    survey_questions = list(config.answer_config.survey_questions or [])
    if not survey_questions:
        raise RuntimePreparationError(
            '未配置任何题目，无法开始执行（请先在"题目配置"页添加/配置题目）',
            log_message="未配置任何题目，无法启动",
        )

    provider = _resolve_survey_provider(config)
    _validate_datetime_window(config, provider)
    _sync_and_validate_random_proxy_config(config)
    try:
        _verify_wjx_survey_is_answerable(config, provider)
    except (SurveyStoppedError, SurveyEnterpriseUnavailableError) as exc:
        raise RuntimePreparationError(
            str(exc),
            log_message=f"启动前问卷状态检查失败：{exc}",
        ) from exc

    if questions_info is None:
        from survey_submitter.providers.registry import parse_survey

        if not config.survey.url:
            raise RuntimePreparationError(
                "未提供问卷 URL，无法解析题目元数据",
                log_message="缺少 survey.url，无法解析 questions_info",
            )
        definition = asyncio.run(parse_survey(config.survey.url))
        questions_info = definition.questions
    assert questions_info is not None
    questions_info_inputs = cast(list[SurveyQuestionMeta | dict[str, Any]], list(questions_info))

    validation_error = validate_question_config(survey_questions, questions_info_inputs)
    if validation_error:
        raise RuntimePreparationError(
            f"题目配置存在冲突，无法启动：\n\n{validation_error}",
            log_message=f"题目配置验证失败：{validation_error}",
        )

    try:
        reverse_fill_spec = build_enabled_reverse_fill_spec(
            config,
            questions_info_inputs,
            survey_questions,
        )
    except Exception as exc:
        raise RuntimePreparationError(
            str(exc), log_message=f"反填配置验证失败：{exc}", detailed=True
        ) from exc

    try:
        set_proxy_occupy_minute_by_answer_duration(
            _extract_answer_duration_for_proxy(config),
            provider=provider,
        )
    except Exception:
        logger.opt(exception=True).debug("同步随机IP占用时长失败")

    execution_config = _build_execution_config_template(
        config,
        title=_resolve_survey_title(config, fallback_survey_title),
        provider=provider,
        reverse_fill_spec=reverse_fill_spec,
        questions_info=questions_info,
    )

    try:
        configure_probabilities(
            survey_questions,
            ctx=execution_config,
            reliability_mode_enabled=bool(config.execution.reliability_mode),
        )
    except Exception as exc:
        raise RuntimePreparationError(str(exc), log_message=f"配置题目失败：{exc}") from exc

    resolved_leases = []
    for raw_address in list(config.execution.proxy.ip_list or []):
        lease = coerce_proxy_lease(raw_address, source="custom")
        if lease is not None:
            resolved_leases.append(lease)

    return PreparedExecutionArtifacts(
        execution_config_template=execution_config,
        proxy_ip_pool=deque(resolved_leases),
        provider=provider,
        survey_questions=list(survey_questions),
        questions_info=questions_info,
        reverse_fill_spec=reverse_fill_spec,
    )


__all__ = [
    "PreparedExecutionArtifacts",
    "RuntimePreparationError",
    "prepare_execution_artifacts",
]
