from __future__ import annotations

import math
import random
from typing import Any


def coerce_non_negative_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (ValueError, TypeError):
        number = int(default)
    return max(0, number)


def valid_forced_choice_index(raw_value: Any, option_count: int) -> int | None:
    try:
        candidate = int(raw_value)
    except (ValueError, TypeError):
        return None
    if 0 <= candidate < option_count:
        return candidate
    return None


def format_weight_value(value: Any) -> str:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return str(value or "").strip() or "随机"
    if math.isnan(number) or math.isinf(number):
        return "随机"
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def positive_multiple_indices(weights: Any, option_count: int) -> list[int]:
    count = max(0, int(option_count or 0))
    if count <= 0:
        return []
    if not isinstance(weights, list) or not weights:
        return [random.randrange(count)]
    normalized: list[float] = []
    for idx in range(count):
        raw = weights[idx] if idx < len(weights) else 0.0
        try:
            normalized.append(max(0.0, float(raw)))
        except (ValueError, TypeError):
            normalized.append(0.0)
    selected = [
        idx
        for idx, weight in enumerate(normalized)
        if weight > 0 and random.uniform(0, 100) <= weight
    ]
    if not selected:
        positive = [idx for idx, weight in enumerate(normalized) if weight > 0]
        selected = [random.choice(positive)] if positive else [random.randrange(count)]
    return selected
