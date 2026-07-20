from __future__ import annotations

import random
import threading
import math
from survey_submitter.logging.log_utils import log_suppressed_exception

from survey_submitter.core.questions.reliability_mode import get_reliability_profile
from survey_submitter.core.questions.utils import weighted_index
from survey_submitter.constants import DIMENSION_UNGROUPED


_thread_local = threading.local()

_SMALL_SCALE_STATIC_MAX_OPTIONS = 3


def reset_tendency() -> None:

    _thread_local.dimension_bases = {}


def _generate_base_ratio(
    option_count: int,
    probabilities: list[float] | int | None,
) -> float:

    if probabilities == -1 or probabilities is None:
        try:
            from survey_submitter.core.persona.generator import get_current_persona

            persona = get_current_persona()
            if persona is not None:
                raw = persona.satisfaction_tendency
                jitter = random.gauss(0, 0.1)
                return max(0.0, min(1.0, raw + jitter))
        except ImportError as exc:
            log_suppressed_exception(
                "_generate_base_ratio: get_current_persona", exc, level="ERROR"
            )
        return random.random()
    if isinstance(probabilities, list) and probabilities:
        idx = weighted_index(probabilities)
        ratio = idx / max(option_count - 1, 1)
        return ratio
    return random.random()


def _is_ungrouped(dimension: str | None) -> bool:

    return dimension is None or dimension == DIMENSION_UNGROUPED


def _random_by_probabilities(option_count: int, probabilities: list[float] | int | None) -> int:

    if isinstance(probabilities, list) and len(probabilities) == option_count:
        return weighted_index(probabilities)
    return random.randrange(option_count)


def _normalize_probabilities_for_zero_guard(
    option_count: int,
    probabilities: list[float] | int | None,
) -> list[float] | None:

    if option_count <= 0 or not isinstance(probabilities, list):
        return None

    normalized: list[float] = []
    for idx in range(option_count):
        raw = probabilities[idx] if idx < len(probabilities) else 0.0
        try:
            weight = float(raw)
        except (ValueError, TypeError):
            weight = 0.0
        if math.isnan(weight) or math.isinf(weight) or weight <= 0.0:
            weight = 0.0
        normalized.append(weight)
    return normalized


def _enforce_zero_weight_guard(
    selected_index: int,
    option_count: int,
    probabilities: list[float] | int | None,
    anchor_index: int | None = None,
) -> int:

    if option_count <= 0:
        return 0

    selected = max(0, min(option_count - 1, int(selected_index)))
    normalized = _normalize_probabilities_for_zero_guard(option_count, probabilities)
    if not normalized:
        return selected

    positive_indices = [idx for idx, weight in enumerate(normalized) if weight > 0.0]
    if not positive_indices:
        raise ValueError(
            "当前题目所有选项权重均为 0，无法在“0 权重禁选”约束下作答，请至少保留一个非 0 选项。"
        )
    if selected in positive_indices:
        return selected

    if anchor_index is None:
        target = selected
    else:
        target = max(0, min(option_count - 1, int(anchor_index)))

    best = positive_indices[0]
    best_distance = abs(best - target)
    best_weight = normalized[best]
    for idx in positive_indices[1:]:
        distance = abs(idx - target)
        weight = normalized[idx]
        if (
            distance < best_distance
            or (distance == best_distance and weight > best_weight)
            or (distance == best_distance and weight == best_weight and idx < best)
        ):
            best = idx
            best_distance = distance
            best_weight = weight

    return best


def get_tendency_index(
    option_count: int,
    probabilities: list[float] | int | None,
    dimension: str | None = None,
    question_index: int | None = None,
    row_index: int | None = None,
) -> int:

    if option_count <= 0:
        return 0

    def _finalize_choice(choice: int, anchor: int | None = None) -> int:
        return _enforce_zero_weight_guard(
            choice,
            option_count,
            probabilities,
            anchor_index=anchor,
        )

    if _is_ungrouped(dimension):
        result = _random_by_probabilities(option_count, probabilities)
        return _finalize_choice(result, anchor=result)

    assert dimension is not None
    bases: dict[str, float] = getattr(_thread_local, "dimension_bases", {})
    if not isinstance(bases, dict):
        bases = {}
        _thread_local.dimension_bases = bases

    base_ratio = bases.get(dimension)

    if base_ratio is None:
        base_ratio = _generate_base_ratio(option_count, probabilities)
        bases[dimension] = base_ratio

    base = int(round(base_ratio * (option_count - 1)))
    base = max(0, min(option_count - 1, base))

    selected = _apply_consistency(base, option_count, probabilities)
    return _finalize_choice(selected, anchor=base)


def _apply_consistency(
    base: int,
    option_count: int,
    probabilities: list[float] | int | None,
) -> int:

    effective_base = min(base, option_count - 1)
    fluctuation_window = _resolve_fluctuation_window(option_count)
    if fluctuation_window <= 0:
        return effective_base

    low = max(0, effective_base - fluctuation_window)
    high = min(option_count - 1, effective_base + fluctuation_window)

    if isinstance(probabilities, list) and len(probabilities) == option_count:
        adjusted_probs = []
        for i in range(option_count):
            if low <= i <= high:
                distance = abs(i - effective_base)
                decay = _window_decay(distance, fluctuation_window)
                adjusted_probs.append(probabilities[i] * decay)
            else:
                adjusted_probs.append(
                    probabilities[i] * get_reliability_profile().consistency_outside_decay
                )

        total = sum(adjusted_probs)
        if total > 0:
            adjusted_probs = [p / total for p in adjusted_probs]
            return weighted_index(adjusted_probs)

    candidates = list(range(low, high + 1))
    weights = []
    for c in candidates:
        distance = abs(c - effective_base)
        weights.append(_window_decay(distance, fluctuation_window))

    total = sum(weights)
    pivot = random.random() * total
    running = 0.0
    for i, w in enumerate(weights):
        running += w
        if pivot <= running:
            return candidates[i]
    return candidates[-1]


def _resolve_fluctuation_window(option_count: int) -> int:

    if option_count <= _SMALL_SCALE_STATIC_MAX_OPTIONS:
        return 0

    profile = get_reliability_profile()
    span = max(option_count, 1)
    window = int(round(span * profile.consistency_window_ratio))
    if window < 1:
        window = 1
    return min(window, profile.consistency_window_max)


def _window_decay(distance: int, window: int) -> float:

    profile = get_reliability_profile()
    if distance <= 0:
        return profile.consistency_center_weight
    if window <= 0:
        return 0.0

    normalized = min(1.0, float(distance) / float(window))
    center = profile.consistency_center_weight
    edge = min(center, profile.consistency_edge_weight)
    return max(edge, center - (center - edge) * normalized)
