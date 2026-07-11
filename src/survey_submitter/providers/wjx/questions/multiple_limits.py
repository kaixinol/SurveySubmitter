from __future__ import annotations

import json
import re

from collections.abc import Sequence

from survey_submitter.providers.match_utils import get_element_attribute, normalize_match_text

_MULTI_LIMIT_ATTRIBUTE_NAMES = (
    "max",
    "maxvalue",
    "maxcount",
    "maxchoice",
    "maxselect",
    "maxsel",
    "maxnum",
    "maxlimit",
    "data-max",
    "data-maxvalue",
    "data-maxcount",
    "data-maxchoice",
    "data-maxselect",
    "data-selectmax",
)

_MULTI_LIMIT_VALUE_KEYS = (
    "max",
    "maxvalue",
    "maxcount",
    "maxchoice",
    "maxselect",
    "selectmax",
)

_MULTI_LIMIT_VALUE_KEYSET = {name.lower() for name in _MULTI_LIMIT_VALUE_KEYS}

_MULTI_MIN_LIMIT_ATTRIBUTE_NAMES = (
    "min",
    "minvalue",
    "mincount",
    "minchoice",
    "minselect",
    "selectmin",
    "minsel",
    "minnum",
    "minlimit",
    "data-min",
    "data-minvalue",
    "data-mincount",
    "data-minchoice",
    "data-minselect",
    "data-selectmin",
)

_MULTI_MIN_LIMIT_VALUE_KEYS = (
    "min",
    "minvalue",
    "mincount",
    "minchoice",
    "minselect",
    "selectmin",
    "minlimit",
)

_MULTI_MIN_LIMIT_VALUE_KEYSET = {name.lower() for name in _MULTI_MIN_LIMIT_VALUE_KEYS}

_SELECTION_KEYWORDS_CN = ("选", "選", "选择", "多选", "复选")
_SELECTION_KEYWORDS_EN = ("option", "options", "choice", "choices", "select", "choose")

_CHINESE_MULTI_LIMIT_PATTERNS = (
    re.compile(r"(?:最多|至多|不超过|不超過)\s*(?:选|選|选择|選擇)?\s*(\d+)\s*[个項项]?"),
    re.compile(r"(?:限选|限選)\s*(\d+)\s*[个項项条]?"),
)

_CHINESE_MULTI_RANGE_PATTERNS = (
    re.compile(
        r"(?:请[选選择擇]?|可选|可選|需选|需選|选择|選擇|勾选|勾選)\s*(\d+)\s*(?:-|－|—|–|~|～|至|到)\s*(\d+)(?:\s*[个項项条])?"
    ),
    re.compile(
        r"至少\s*(\d+)\s*[个項项条]?(?:[^0-9]{0,6})(?:最多|至多|不超过|不超過)\s*(\d+)\s*[个項项条]?"
    ),
    re.compile(r"(?:限选|限選)\s*(\d+)\s*(?:-|－|—|–|~|～|至|到)\s*(\d+)(?:\s*[个項项条])?"),
)

_CHINESE_MULTI_EXACT_PATTERNS = (
    re.compile(r"(?:请)?(?:选|選|选择|選擇|勾选|勾選)\s*(\d+)\s*[个項项条]"),
    re.compile(r"(?:必须|需|需要)\s*(?:选|選|选择|選擇|勾选|勾選)\s*(\d+)\s*[个項项条]"),
)

_CHINESE_MULTI_MIN_PATTERNS = (
    re.compile(r"(?:至少|最少|不少于)\s*(?:选|選|选择|選擇)?\s*(\d+)\s*[个項项条]"),
)

_ENGLISH_MULTI_LIMIT_PATTERNS = (
    re.compile(
        r"(?:select|choose|pick)\s+(?:up\s+to|at\s+most|no\s+more\s+than)\s+(\d+)", re.IGNORECASE
    ),
    re.compile(
        r"(?:up\s+to|at\s+most|no\s+more\s+than)\s+(\d+)\s+(?:options?|choices?|items?)",
        re.IGNORECASE,
    ),
)

_ENGLISH_MULTI_RANGE_PATTERNS = (
    re.compile(r"(?:select|choose|pick)\s*(\d+)\s*(?:-|–|—|~|～|to)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:select|choose)\s+between\s+(\d+)\s+and\s+(\d+)", re.IGNORECASE),
)

_ENGLISH_MULTI_EXACT_PATTERNS = (
    re.compile(r"(?:select|choose|pick)\s+(\d+)\s+(?:options?|choices?|items?)", re.IGNORECASE),
    re.compile(r"(?:must|need\s+to|please)\s+(?:select|choose|pick)\s+(\d+)", re.IGNORECASE),
)

_ENGLISH_MULTI_MIN_PATTERNS = (
    re.compile(r"(?:at\s+least|min(?:imum)?\s*)\s*(\d+)", re.IGNORECASE),
)


def _compile_key_value_patterns(keys) -> tuple[re.Pattern[str], ...]:
    return tuple(
        re.compile(rf"{re.escape(str(key))}\s*[:=]\s*(\d+)", re.IGNORECASE) for key in sorted(keys)
    )


_MULTI_MIN_VALUE_PATTERNS = _compile_key_value_patterns(_MULTI_MIN_LIMIT_VALUE_KEYSET)
_MULTI_MAX_VALUE_PATTERNS = _compile_key_value_patterns(_MULTI_LIMIT_VALUE_KEYSET)


def _safe_positive_int(value: str | int | float | None) -> int | None:

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        int_value = int(value)
        return int_value if int_value > 0 else None
    text = normalize_match_text(value)
    if not text:
        return None
    if text.isdigit():
        int_value = int(text)
        return int_value if int_value > 0 else None
    match = re.search(r"(\d+)", text)
    if match:
        int_value = int(match.group(1))
        return int_value if int_value > 0 else None
    return None


def _extract_range_from_json_obj(
    obj: dict[str, object] | list[object],
) -> tuple[int | None, int | None]:

    min_limit: int | None = None
    max_limit: int | None = None
    if isinstance(obj, dict):
        for key, value in obj.items():
            normalized_key = str(key).lower()
            if normalized_key in _MULTI_MIN_LIMIT_VALUE_KEYSET:
                candidate = _safe_positive_int(value)  # ty: ignore[invalid-argument-type]
                if candidate:
                    min_limit = min_limit or candidate
            if normalized_key in _MULTI_LIMIT_VALUE_KEYSET:
                candidate = _safe_positive_int(value)  # ty: ignore[invalid-argument-type]
                if candidate:
                    max_limit = max_limit or candidate
            nested_min, nested_max = _extract_range_from_json_obj(value)  # ty: ignore[invalid-argument-type]
            if min_limit is None and nested_min is not None:
                min_limit = nested_min
            if max_limit is None and nested_max is not None:
                max_limit = nested_max
            if min_limit is not None and max_limit is not None:
                break
    elif isinstance(obj, list):
        for item in obj:
            nested_min, nested_max = _extract_range_from_json_obj(item)  # ty: ignore[invalid-argument-type]
            if min_limit is None and nested_min is not None:
                min_limit = nested_min
            if max_limit is None and nested_max is not None:
                max_limit = nested_max
            if min_limit is not None and max_limit is not None:
                break
    return min_limit, max_limit


def _extract_range_from_possible_json(text: str | None) -> tuple[int | None, int | None]:

    min_limit: int | None = None
    max_limit: int | None = None
    if not text:
        return min_limit, max_limit
    normalized = normalize_match_text(text)
    if not normalized:
        return min_limit, max_limit
    candidates = [normalized]
    if normalized.startswith("{") and "'" in normalized and '"' not in normalized:
        candidates.append(normalized.replace("'", '"'))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        cand_min, cand_max = _extract_range_from_json_obj(parsed)
        if min_limit is None and cand_min is not None:
            min_limit = cand_min
        if max_limit is None and cand_max is not None:
            max_limit = cand_max
        if min_limit is not None and max_limit is not None:
            return min_limit, max_limit
    for pattern in _MULTI_MIN_VALUE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            candidate = _safe_positive_int(match.group(1))
            if candidate:
                min_limit = min_limit or candidate
                if max_limit is not None:
                    return min_limit, max_limit
    for pattern in _MULTI_MAX_VALUE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            candidate = _safe_positive_int(match.group(1))
            if candidate:
                max_limit = max_limit or candidate
                if min_limit is not None:
                    return min_limit, max_limit
    return min_limit, max_limit


def _extract_min_max_from_attributes(element) -> tuple[int | None, int | None]:

    min_limit = None
    max_limit = None
    for attr in _MULTI_MIN_LIMIT_ATTRIBUTE_NAMES:
        raw_value = get_element_attribute(element, attr)
        candidate = _safe_positive_int(raw_value)
        if candidate:
            min_limit = candidate
            break
    for attr in _MULTI_LIMIT_ATTRIBUTE_NAMES:
        raw_value = get_element_attribute(element, attr)
        candidate = _safe_positive_int(raw_value)
        if candidate:
            max_limit = candidate
            break
    return min_limit, max_limit


def _try_pattern_range(patterns: Sequence, text: str) -> tuple[int | None, int | None]:
    """Try to match range patterns (e.g., '5-10') and return min, max."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            first = _safe_positive_int(match.group(1))
            second = _safe_positive_int(match.group(2))
            if first and second:
                return min(first, second), max(first, second)
    return None, None


def _try_pattern_exact(patterns: Sequence, text: str) -> int | None:
    """Try to match exact value patterns and return the value."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            candidate = _safe_positive_int(match.group(1))
            if candidate:
                return candidate
    return None


def _extract_multi_limit_range_from_text(text: str | None) -> tuple[int | None, int | None]:

    if not text:
        return None, None
    normalized = normalize_match_text(text)
    if not normalized:
        return None, None
    normalized_lower = normalized.lower()

    # Check for language-specific keywords
    contains_cn_keyword = any(keyword in normalized for keyword in _SELECTION_KEYWORDS_CN)
    contains_en_keyword = any(keyword in normalized_lower for keyword in _SELECTION_KEYWORDS_EN)
    contains_cn_min_hint = any(keyword in normalized for keyword in ("至少", "最少", "不少于"))
    contains_cn_max_hint = any(
        keyword in normalized for keyword in ("最多", "至多", "不超过", "不超過", "限选", "限選")
    )
    contains_en_min_hint = any(keyword in normalized_lower for keyword in ("at least", "minimum"))
    contains_en_max_hint = any(
        keyword in normalized_lower for keyword in ("up to", "at most", "no more than")
    )

    min_limit: int | None = None
    max_limit: int | None = None

    # Step 1: Try range patterns (e.g., "选择5-10个")
    if contains_cn_keyword:
        min_limit, max_limit = _try_pattern_range(_CHINESE_MULTI_RANGE_PATTERNS, normalized)
    if min_limit is None and max_limit is None and contains_en_keyword:
        min_limit, max_limit = _try_pattern_range(_ENGLISH_MULTI_RANGE_PATTERNS, normalized_lower)

    # Step 2: Try exact patterns (for non-range cases)
    if (
        min_limit is None
        and max_limit is None
        and contains_cn_keyword
        and not contains_cn_min_hint
        and not contains_cn_max_hint
    ):
        exact = _try_pattern_exact(_CHINESE_MULTI_EXACT_PATTERNS, normalized)
        if exact:
            min_limit = max_limit = exact
    if (
        min_limit is None
        and max_limit is None
        and contains_en_keyword
        and not contains_en_min_hint
        and not contains_en_max_hint
    ):
        exact = _try_pattern_exact(_ENGLISH_MULTI_EXACT_PATTERNS, normalized_lower)
        if exact:
            min_limit = max_limit = exact

    # Step 3: Try min-only patterns
    if min_limit is None and contains_cn_keyword:
        min_limit = _try_pattern_exact(_CHINESE_MULTI_MIN_PATTERNS, normalized)
    if min_limit is None and contains_en_keyword:
        min_limit = _try_pattern_exact(_ENGLISH_MULTI_MIN_PATTERNS, normalized_lower)

    # Step 4: Try max-only patterns
    if max_limit is None and contains_cn_keyword:
        max_limit = _try_pattern_exact(_CHINESE_MULTI_LIMIT_PATTERNS, normalized)
    if max_limit is None and contains_en_keyword:
        max_limit = _try_pattern_exact(_ENGLISH_MULTI_LIMIT_PATTERNS, normalized_lower)

    # Ensure min <= max
    if min_limit is not None and max_limit is not None and min_limit > max_limit:
        min_limit, max_limit = max_limit, min_limit

    return min_limit, max_limit
