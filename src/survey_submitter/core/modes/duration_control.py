from __future__ import annotations

import random
from typing import Any, Protocol

from loguru import logger

from survey_submitter.core.engine.async_wait import sleep_or_stop
from survey_submitter.core.engine.stop_signal import StopSignalLike
from survey_submitter.logging.log_utils import log_suppressed_exception


class _WebElementLike(Protocol):
    async def is_displayed(self) -> bool: ...
    async def text(self) -> str: ...


class _DriverLike(Protocol):
    async def current_url(self) -> str: ...
    async def find_element(self, by: str, value: str) -> Any: ...
    async def execute_script(self, script: str, *args: Any) -> Any: ...

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
    provider: str | None = None,
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
    logger.info(
        f"[Action Log] Simulating answer duration: waiting {wait_seconds:.1f} seconds before submit"
    )
    return bool(await sleep_or_stop(stop_signal, wait_seconds))


async def simulate_answer_duration_delay(
    stop_signal: StopSignalLike | None = None,
    answer_duration_range_seconds: tuple[int, int] = (0, 0),
    *,
    provider: str | None = None,
) -> bool:

    wait_seconds = sample_answer_duration_seconds(
        answer_duration_range_seconds,
        provider=provider,
    )
    return await wait_answer_duration_seconds(stop_signal, wait_seconds)


async def is_survey_completion_page(driver: _DriverLike, provider: str | None = None) -> bool:
    try:
        current_url = str(await driver.current_url() or "")
        if "complete" in current_url.lower():
            return True
    except Exception as exc:
        log_suppressed_exception("is_survey_completion_page: current_url", exc, level="WARNING")

    try:
        from survey_submitter.providers.registry import is_completion_page as _provider_is_completion_page

        if await _provider_is_completion_page(driver, provider=provider):
            return True
    except Exception as exc:
        log_suppressed_exception("is_survey_completion_page: provider_is_completion_page", exc, level="WARNING")

    detected = False
    try:
        completion_div = None
        try:
            completion_div = await driver.find_element("id", "completion_div")
        except Exception:
            completion_div = None
        if completion_div and await completion_div.is_displayed():
            text = await completion_div.text() or ""
            if any(marker in text for marker in _COMPLETION_MARKERS):
                detected = True
    except Exception as exc:
        log_suppressed_exception("is_survey_completion_page: divdsc = None", exc, level="WARNING")
    if not detected:
        for attempt in range(2):
            try:
                page_text = await driver.execute_script(
                    "return (document.body && document.body.innerText) || '';"
                ) or ""
                has_marker = any(marker in page_text for marker in _COMPLETION_MARKERS)
                if has_marker:
                    action_visible = bool(
                        await driver.execute_script(
                            r"""
                            return (() => {
                                const selectors = [
                                    '#submit_button',
                                    '#divSubmit',
                                    '#ctlNext',
                                    '#divNext',
                                    '#btnNext',
                                    '#SM_BTN_1',
                                    '#SubmitBtnGroup .submitbtn',
                                    '.btn-next',
                                    '.btn-submit',
                                    '.page-control button',
                                    'button[type="submit"]',
                                    'a.button.mainBgColor'
                                ];
                                const visible = (el) => {
                                    if (!el) return false;
                                    const style = window.getComputedStyle(el);
                                    if (!style) return false;
                                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                                    const rect = el.getBoundingClientRect();
                                    return rect.width > 0 && rect.height > 0;
                                };
                                for (const sel of selectors) {
                                    const nodes = document.querySelectorAll(sel);
                                    for (const node of nodes) {
                                        if (visible(node)) return true;
                                    }
                                }
                                return false;
                            })();
                            """
                        )
                    )
                    detected = not action_visible
                break
            except Exception as exc:
                if _is_navigation_transient_error(exc):
                    if attempt == 0:
                        await sleep_or_stop(None, 0.2)
                        continue
                    logger.debug(
                        f"[Suppressed] is_survey_completion_page: page_text during navigation: {exc}"
                    )
                    break
                log_suppressed_exception("is_survey_completion_page: page_text", exc, level="WARNING")
                break
    return bool(detected)
