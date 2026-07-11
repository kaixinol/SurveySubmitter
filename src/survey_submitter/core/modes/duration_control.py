from __future__ import annotations

import logging
import random

from survey_submitter.core.engine.async_wait import sleep_or_stop
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.logging.log_utils import log_suppressed_exception

_COMPLETION_MARKERS = (
    "答卷已经提交",
    "感谢您的参与",
    "问卷提交成功",
    "提交成功",
    "已完成本次问卷",
    "已完成本次答卷",
    "感谢您的宝贵时间",
    "问卷已结束",
)
_NAVIGATION_TRANSIENT_ERRORS = (
    "execution context was destroyed",
    "most likely because of a navigation",
)


def _is_navigation_transient_error(exc: BaseException) -> bool:

    message = str(exc or "").lower()
    return any(pattern in message for pattern in _NAVIGATION_TRANSIENT_ERRORS)


def has_configured_answer_duration(answer_duration_range_seconds: tuple[int, int] = (0, 0)) -> bool:
    try:
        raw_min, raw_max = answer_duration_range_seconds
    except (ValueError, TypeError):
        return False
    return max(0, int(raw_min), int(raw_max)) > 0


def sample_answer_duration_seconds(
    answer_duration_range_seconds: tuple[int, int] = (0, 0),
    *,
    survey_provider: str | None = None,
    default_unconfigured_seconds: int = 0,
) -> float:
    try:
        raw_min, raw_max = answer_duration_range_seconds
    except (ValueError, TypeError):
        raw_min = raw_max = default_unconfigured_seconds
    if not has_configured_answer_duration(answer_duration_range_seconds):
        if default_unconfigured_seconds <= 0:
            return 0.0
        raw_min = raw_max = default_unconfigured_seconds

    min_delay = max(0, int(raw_min))
    max_delay = max(min_delay, int(raw_max))

    if min_delay == max_delay:
        base = max_delay
        jitter = max(5, int(base * 0.2))
        min_delay = max(0, base - jitter)
        max_delay = base + jitter

    center = (min_delay + max_delay) / 2.0
    std_dev = (max_delay - min_delay) / 6.0 if max_delay > min_delay else 0.0

    if std_dev > 0:
        wait_seconds = random.gauss(center, std_dev)
    else:
        wait_seconds = float(min_delay)

    return max(min_delay, min(max_delay, wait_seconds))


async def wait_answer_duration_seconds(
    stop_signal: StopSignalLike | None = None,
    seconds: float = 0.0,
) -> bool:

    wait_seconds = max(0.0, float(seconds or 0.0))
    if wait_seconds <= 0:
        return False
    logging.info(
        "[Action Log] Simulating answer duration: waiting %.1f seconds before submit",
        wait_seconds,
    )
    return bool(await sleep_or_stop(stop_signal, wait_seconds))


async def simulate_answer_duration_delay(
    stop_signal: StopSignalLike | None = None,
    answer_duration_range_seconds: tuple[int, int] = (0, 0),
    *,
    survey_provider: str | None = None,
) -> bool:

    wait_seconds = sample_answer_duration_seconds(
        answer_duration_range_seconds,
        survey_provider=survey_provider,
    )
    return await wait_answer_duration_seconds(stop_signal, wait_seconds)
