from __future__ import annotations

import random
from typing import Any

from survey_submitter.constants import DEFAULT_FILL_TEXT


def option_requires_fill(question: Any, option_index: int) -> bool:
    try:
        fillable_indices = list(getattr(question, "fillable_options", None) or [])
    except Exception:
        fillable_indices = []
    for raw_index in fillable_indices:
        try:
            if int(raw_index) == int(option_index):
                return True
        except (ValueError, TypeError):
            continue
    return False


def option_fill_is_required(question: Any, option_index: int) -> bool:
    try:
        required_indices = list(getattr(question, "required_fillable_options", None) or [])
    except Exception:
        required_indices = []
    for raw_index in required_indices:
        try:
            if int(raw_index) == int(option_index):
                return True
        except (ValueError, TypeError):
            continue
    return False


def should_skip_optional_option_fill(
    question: Any,
    option_index: int,
    skip_ratio: float,
) -> bool:
    """Decide whether to leave an attached-text fill empty for this option.

    Only optional fills (fillable but not required) can be skipped. Required
    fills and non-fillable options are never skipped. ``skip_ratio`` is
    clamped to ``[0, 1]``; ``0`` disables skipping.
    """
    if not option_requires_fill(question, option_index):
        return False
    if option_fill_is_required(question, option_index):
        return False
    try:
        ratio = min(1.0, max(0.0, float(skip_ratio)))
    except (ValueError, TypeError):
        ratio = 0.0
    return ratio > 0.0 and random.random() < ratio


def default_missing_option_fill(
    question: Any, option_index: int, fill_value: str | None
) -> str | None:
    if str(fill_value or "").strip():
        return str(fill_value or "").strip()
    if option_requires_fill(question, option_index):
        return DEFAULT_FILL_TEXT
    return None
