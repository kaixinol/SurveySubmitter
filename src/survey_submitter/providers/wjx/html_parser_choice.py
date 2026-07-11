from __future__ import annotations

import re

from survey_submitter.providers.match_utils import normalize_match_text
from .html_parser_common import (
    _is_select_placeholder_option,
    _normalize_html_text,
    _text_looks_like_select_placeholder,
)
from .regexes import (
    WJX_FORCE_SELECT_CLEAN_RE,
    WJX_FORCE_SELECT_COMMAND_RE,
    WJX_FORCE_SELECT_INDEX_TARGET_RE,
    WJX_FORCE_SELECT_LABEL_TARGET_RE,
    WJX_FORCE_SELECT_OPTION_LABEL_RE,
    WJX_FORCE_SELECT_SENTENCE_SPLIT_RE,
)

_TEXT_INPUT_ALLOWED_TYPES = {"", "text", "search", "tel", "number"}


def _normalize_force_select_text(value: str | None) -> str:
    text = normalize_match_text(value)
    if not text:
        return ""
    return WJX_FORCE_SELECT_CLEAN_RE.sub("", text).lower()


def _extract_force_select_option_label(option_text: str | None) -> str | None:
    text = normalize_match_text(option_text)
    if not text:
        return None
    match = WJX_FORCE_SELECT_OPTION_LABEL_RE.match(text)
    if not match:
        return None
    label = str(match.group("label") or "").strip().upper()
    return label or None


def _collect_force_select_fragments(question_div, title_text: str) -> list[str]:
    fragments: list[str] = []
    if title_text:
        cleaned_title = _normalize_html_text(title_text)
        if cleaned_title:
            fragments.append(cleaned_title)
    if question_div is None:
        return fragments
    for selector in (".qtypetip", ".topichtml", ".field-label"):
        element = question_div.select_one(selector)
        if not element:
            continue
        text = _normalize_html_text(element.get_text(" ", strip=True))
        if text:
            fragments.append(text)
    unique_fragments: list[str] = []
    seen: set = set()
    for fragment in fragments:
        key = _normalize_html_text(fragment)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_fragments.append(key)
    return unique_fragments


def _extract_force_select_option(
    question_div,
    title_text: str,
    option_texts: list[str],
) -> tuple[int | None, str | None]:

    if not option_texts:
        return None, None

    normalized_options: list[tuple[int, str, str]] = []
    for idx, option_text in enumerate(option_texts):
        normalized = _normalize_force_select_text(option_text)
        if not normalized:
            continue
        normalized_options.append((idx, str(option_text or "").strip(), normalized))
    if not normalized_options:
        return None, None

    fragments = _collect_force_select_fragments(question_div, title_text)
    for fragment in fragments:
        for command_match in WJX_FORCE_SELECT_COMMAND_RE.finditer(fragment):
            tail_text = fragment[command_match.end() :]
            if not tail_text:
                continue
            sentence = WJX_FORCE_SELECT_SENTENCE_SPLIT_RE.split(tail_text, maxsplit=1)[0]
            sentence = sentence.strip(" ：:，,、")
            if not sentence:
                continue
            normalized_sentence = normalize_match_text(sentence)
            compact_sentence = _normalize_force_select_text(sentence)
            if not compact_sentence:
                continue

            index_match = WJX_FORCE_SELECT_INDEX_TARGET_RE.fullmatch(normalized_sentence)
            if index_match:
                try:
                    target_idx = int(index_match.group("index")) - 1
                except (ValueError, TypeError):
                    target_idx = -1
                if 0 <= target_idx < len(option_texts):
                    selected = str(option_texts[target_idx] or "").strip()
                    return target_idx, selected or None

            label_match = WJX_FORCE_SELECT_LABEL_TARGET_RE.fullmatch(compact_sentence)
            if label_match:
                target_label = str(label_match.group("label") or "").strip().upper()
                if target_label:
                    for option_idx, raw_text, _ in normalized_options:
                        option_label = _extract_force_select_option_label(raw_text)
                        if option_label == target_label:
                            return option_idx, raw_text

            exact_candidates = sorted(
                (
                    (option_idx, raw_text, normalized_text)
                    for option_idx, raw_text, normalized_text in normalized_options
                    if not normalized_text.isdigit() and normalized_text == compact_sentence
                ),
                key=lambda item: len(item[2]),
                reverse=True,
            )
            if exact_candidates:
                option_idx, raw_text, _ = exact_candidates[0]
                return option_idx, raw_text
    return None, None


def _is_text_input_element(element) -> bool:
    if element is None:
        return False
    tag_name = (element.name or "").lower()
    input_type = (element.get("type") or "").lower()
    if tag_name == "textarea":
        return True
    return tag_name == "input" and input_type in ("", "text", "search", "tel", "number")


def _element_contains_text_input(element) -> bool:
    if element is None:
        return False
    if _is_text_input_element(element):
        return True
    candidates = element.find_all(["input", "textarea"])
    for candidate in candidates:
        if _is_text_input_element(candidate):
            return True
    return False


def _question_div_has_shared_text_input(question_div) -> bool:
    if question_div is None:
        return False
    shared_inputs = question_div.select(".ui-other input, .ui-other textarea")
    if any(_element_contains_text_input(element) for element in shared_inputs):
        return True
    keyword_inputs = question_div.select(
        "input[id*='other'], input[name*='other'], textarea[id*='other'], textarea[name*='other']"
    )
    if any(_element_contains_text_input(element) for element in keyword_inputs):
        return True
    return False


def _extract_option_text_from_attrs(target) -> str:
    if target is None:
        return ""

    def _get_attr_text(node, keys) -> str:
        for key in keys:
            raw = node.get(key)
            if raw is None:
                continue
            text_value = _normalize_html_text(str(raw))
            if text_value:
                return text_value
        return ""

    primary_keys = ("title", "data-title", "data-text", "data-label", "aria-label", "alt", "htitle")
    text_value = _get_attr_text(target, primary_keys)
    if text_value:
        return text_value

    candidates = target.find_all(["a", "span", "label"], limit=4)
    for child in candidates:
        text_value = _get_attr_text(child, primary_keys)
        if text_value:
            return text_value

    fallback_keys = ("val", "value", "data-value", "data-val")
    text_value = _get_attr_text(target, fallback_keys)
    if text_value:
        return text_value
    for child in candidates:
        text_value = _get_attr_text(child, fallback_keys)
        if text_value:
            return text_value
    return ""


def _text_looks_meaningful(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text))


def _extract_rating_option_texts(question_div) -> list[str]:

    if question_div is None:
        return []
    selectors = (
        ".scale-rating ul li a",
        ".scale-rating a[val]",
        "ul[tp='d'] li a",
        "ul[class*='modlen'] li a",
    )
    anchors: list[Any] = []
    for selector in selectors:
        anchors = question_div.select(selector)
        if anchors:
            break
    if not anchors:
        return []
    texts: list[str] = []
    seen = set()
    for idx, anchor in enumerate(anchors):
        text = _extract_option_text_from_attrs(anchor)
        if not _text_looks_meaningful(text):
            text = _normalize_html_text(anchor.get_text(" ", strip=True))
        if not _text_looks_meaningful(text):
            text = _normalize_html_text(anchor.get("title") or "")
        if not _text_looks_meaningful(text):
            text = _normalize_html_text(anchor.get("val") or "")
        if not _text_looks_meaningful(text):
            text = str(idx + 1)
        if text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def _collect_choice_option_texts(question_div) -> tuple[list[str], list[int]]:
    texts: list[str] = []
    fillable_indices: list[int] = []
    option_elements: list[Any] = []
    selectors = [".ui-controlgroup > div", "ul > li"]
    for selector in selectors:
        option_elements = question_div.select(selector)
        if option_elements:
            break
    if option_elements:
        for element in option_elements:
            label_element = element.select_one(".label")
            if not label_element:
                label_element = element
            text = _normalize_html_text(label_element.get_text(" ", strip=True))
            if not text:
                text = _extract_option_text_from_attrs(element)
            if not text:
                continue
            option_index = len(texts)
            texts.append(text)
            if _element_contains_text_input(element):
                fillable_indices.append(option_index)
    if not texts:
        seen = set()
        fallback_selectors = [".label", "li span", "li"]
        for selector in fallback_selectors:
            elements = question_div.select(selector)
            for element in elements:
                text = _normalize_html_text(element.get_text(" ", strip=True))
                if not text:
                    text = _extract_option_text_from_attrs(element)
                if not text or text in seen:
                    continue
                texts.append(text)
                seen.add(text)
            if texts:
                break
    if not fillable_indices and texts and _question_div_has_shared_text_input(question_div):
        fillable_indices.append(len(texts) - 1)
    fillable_indices = sorted(set(fillable_indices))
    return texts, fillable_indices


def _extract_select_option_texts_from_element(select_element) -> list[str]:
    if select_element is None:
        return []
    options: list[str] = []
    option_elements = select_element.find_all("option")
    for idx, option in enumerate(option_elements):
        value = _normalize_html_text(option.get("value") or "")
        text = _normalize_html_text(option.get_text(" ", strip=True))
        if _is_select_placeholder_option(idx, value, text):
            continue
        if not text:
            continue
        options.append(text)
    return options


def _extract_custom_select_option_texts(element) -> list[str]:
    if element is None:
        return []
    raw_values: list[str] = []
    attr_keys = ("cusom", "custom", "data-custom", "data-cusom")
    for key in attr_keys:
        raw = element.get(key)
        if raw is not None:
            raw_values.append(str(raw))
    options: list[str] = []
    for raw in raw_values:
        for part in re.split(r"[,，\n\r|/]+", raw):
            text = _normalize_html_text(part)
            if not text or _text_looks_like_select_placeholder(text):
                continue
            options.append(text)
    deduped: list[str] = []
    seen = set()
    for option in options:
        if option in seen:
            continue
        seen.add(option)
        deduped.append(option)
    return deduped


def _extract_choice_attached_selects(question_div) -> list[dict[str, Any]]:
    if question_div is None:
        return []
    option_elements: list[Any] = []
    for selector in (".ui-controlgroup > div", "ul > li"):
        option_elements = question_div.select(selector)
        if option_elements:
            break
    attached_selects: list[dict[str, Any]] = []
    for option_index, element in enumerate(option_elements):
        option_text = ""
        label_element = element.select_one(".label")
        if label_element is not None:
            option_text = _normalize_html_text(label_element.get_text(" ", strip=True))
        if not option_text:
            option_text = _extract_option_text_from_attrs(element)
        select_element = element.find("select")
        select_options = _extract_select_option_texts_from_element(select_element)
        if not select_options:
            input_candidates = element.find_all("input")
            for input_element in input_candidates:
                select_options = _extract_custom_select_option_texts(input_element)
                if select_options:
                    break
        if not select_options:
            continue
        attached_selects.append(
            {
                "option_index": option_index,
                "option_text": option_text,
                "select_options": select_options,
                "select_option_count": len(select_options),
            }
        )
    return attached_selects


def _verify_text_indicates_location(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    return (
        ("地图" in text)
        or ("省市" in text)
        or ("省份" in text)
        or ("城市" in text)
        or ("地区" in text)
        or ("高校" in text)
    )


def _soup_question_is_location(question_div) -> bool:
    if question_div is None:
        return False
    if question_div.find(class_="get_Local"):
        return True
    inputs = question_div.find_all("input")
    for input_element in inputs:
        verify_value = input_element.get("verify")
        if _verify_text_indicates_location(verify_value):
            return True
    return False


def _extract_location_verify_type(question_div) -> str:
    """Extract the verify attribute value from a location question to distinguish sub-types.

    Returns the verify value string (e.g. '省市区' or '高校'), or empty string if not found.
    """
    if question_div is None:
        return ""
    inputs = question_div.find_all("input")
    for input_element in inputs:
        verify_value = str(input_element.get("verify") or "").strip()
        if verify_value:
            return verify_value
    return ""


def _collect_select_option_texts(question_div, soup, question_number: int) -> list[str]:
    select = question_div.find("select")
    if not select and soup:
        select = soup.find("select", id=f"q{question_number}")
    if not select:
        return []
    options: list[str] = []
    option_elements = select.find_all("option")
    for idx, option in enumerate(option_elements):
        value = (option.get("value") or "").strip()
        text = _normalize_html_text(option.get_text(" ", strip=True))
        if _is_select_placeholder_option(idx, value, text):
            continue
        if not text:
            continue
        options.append(text)
    return options
