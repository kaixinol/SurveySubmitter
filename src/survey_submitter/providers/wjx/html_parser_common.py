from __future__ import annotations

import html as html_lib
import re

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # ty: ignore[invalid-assignment]

from survey_submitter.core.questions.types import TypeCode
from survey_submitter.core.questions.utils import _normalize_question_type_code
from survey_submitter.providers.match_utils import normalize_match_text

from .regexes import WJX_MODLEN_CLASS_RE, WJX_QUESTION_PREFIX_RE, WJX_TITLE_SUFFIX_RE

_TEXT_INPUT_ALLOWED_TYPES = {"text", "tel", "email", "number", "search", "url", "password"}
_KNOWN_NON_TEXT_QUESTION_TYPES = {
    TypeCode.SINGLE,
    TypeCode.MULTIPLE,
    TypeCode.SCORE,
    TypeCode.SCALE,
    TypeCode.MATRIX,
    TypeCode.DROPDOWN,
    TypeCode.SLIDER,
    TypeCode.ORDER,
}
_SELECT_PLACEHOLDER_PREFIXES = ("请选择", "请先选择")
_LOCATION_VERIFY_MARKERS = (
    "地图",
    "省市",
    "省份",
    "城市",
    "地区",
    "高校",
    "map",
    "city",
    "province",
    "area",
)
_DISPLAY_SPACE_RE = re.compile(r"\s+")


def _normalize_html_text(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    text = html_lib.unescape(text)
    text = _DISPLAY_SPACE_RE.sub(" ", text)
    return text.strip()


def _extract_prefixed_question_number(raw_title: str | None) -> int | None:
    text = normalize_match_text(raw_title)
    if not text:
        return None
    match = WJX_QUESTION_PREFIX_RE.match(text)
    if not match:
        return None
    number_text = match.group("cn_num") or match.group("q_num") or match.group("plain_num") or ""
    try:
        number = int(number_text)
    except (ValueError, TypeError):
        return None
    return number if number > 0 else None


def _text_looks_like_select_placeholder(value: str | None) -> bool:
    text = _normalize_html_text(str(value or ""))
    if not text:
        return False
    compact_text = text.replace(" ", "")
    return any(compact_text.startswith(prefix) for prefix in _SELECT_PLACEHOLDER_PREFIXES)


def _is_select_placeholder_option(index: int, value: str | None, text: str | None) -> bool:
    if index != 0:
        return False
    normalized_value = _normalize_html_text(str(value or ""))
    normalized_text = _normalize_html_text(str(text or ""))
    if not normalized_text:
        return True
    if normalized_value in {"", "0", "-1", "-2"}:
        return True
    return _text_looks_like_select_placeholder(normalized_text)


def _input_looks_like_location(input_element) -> bool:
    if input_element is None:
        return False
    verify_value = str(input_element.get("verify") or "").strip()
    onclick_value = str(input_element.get("onclick") or "").strip().lower()
    if not verify_value and "opencitybox" not in onclick_value:
        return False
    if any(
        marker in verify_value
        for marker in _LOCATION_VERIFY_MARKERS
        if marker in {"地图", "省市", "省份", "城市", "地区", "高校"}
    ):
        return True
    return "opencitybox" in onclick_value


def _soup_question_is_required(question_div) -> bool:
    if question_div is None:
        return False
    if str(question_div.get("req") or "").strip() in {"1", "true", "True"}:
        return True
    if str(question_div.get("required") or "").strip().lower() in {"1", "true", "required"}:
        return True
    if str(question_div.get("must") or "").strip() in {"1", "true", "True"}:
        return True
    if str(question_div.get("wjxreq") or "").strip() in {"1", "true", "True"}:
        return True
    if str(question_div.get("aria-required") or "").strip().lower() == "true":
        return True

    marker_selectors = (
        ".req",
        ".required",
        ".must",
        ".star",
        ".red",
        ".wjxreq",
        "[aria-required='true']",
    )
    for selector in marker_selectors:
        if question_div.select_one(selector):
            return True

    heading_text = _extract_display_heading_text(question_div)
    normalized_heading = _normalize_html_text(heading_text)
    if normalized_heading.startswith("*"):
        return True
    if "必答" in normalized_heading:
        return True

    text = _normalize_html_text(question_div.get_text(" ", strip=True))
    if text.startswith("*"):
        return True
    return False


def extract_survey_title_from_html(html: str) -> str | None:

    if not BeautifulSoup:
        return None
    soup = BeautifulSoup(html, "html.parser")

    selectors = [
        "#divTitle h1",
        "#divTitle",
        ".surveytitle",
        ".survey-title",
        ".surveyTitle",
        ".wjdcTitle",
        ".htitle",
        ".topic_tit",
        "#htitle",
        "#lbTitle",
    ]
    candidates: list[str] = []
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            text = _normalize_html_text(element.get_text(" ", strip=True))
            if text:
                candidates.append(text)

    if not candidates:
        for tag_name in ("h1", "h2"):
            header = soup.find(tag_name)
            if header:
                text = _normalize_html_text(header.get_text(" ", strip=True))
                if text:
                    candidates.append(text)
                if candidates:
                    break

    title_tag = soup.find("title")
    if title_tag:
        text = _normalize_html_text(title_tag.get_text(" ", strip=True))
        if text:
            candidates.append(text)

    for raw in candidates:
        cleaned = raw
        cleaned = WJX_TITLE_SUFFIX_RE.sub("", cleaned)
        cleaned = cleaned.strip(" -_|")
        if cleaned:
            return cleaned
    return None


def _extract_question_number_from_div(question_div) -> int | None:
    topic_attr = question_div.get("topic")
    if topic_attr and topic_attr.isdigit():
        return int(topic_attr)
    id_attr = question_div.get("id") or ""
    match = re.search(r"div(\d+)", id_attr)
    if match:
        return int(match.group(1))
    return None


def _cleanup_question_title(raw_title: str) -> str:
    title = _normalize_html_text(raw_title)
    if not title:
        return ""
    title = WJX_QUESTION_PREFIX_RE.sub("", title)
    title = title.replace("【单选题】", "").replace("【多选题】", "")
    return title.strip()


def _extract_display_question_number(raw_title: str | None) -> int | None:
    return _extract_prefixed_question_number(raw_title)


def _extract_display_heading_text(question_div) -> str:
    if question_div is None:
        return ""
    field_label = question_div.find(class_="field-label")
    if field_label is not None:
        parts: list[str] = []
        for class_name in ("topicnumber", "topichtml"):
            element = field_label.find(class_=class_name)
            if element is None:
                continue
            text = element.get_text(" ", strip=True)
            text = _normalize_html_text(text)
            if text:
                parts.append(text)
        if parts:
            return _normalize_html_text(" ".join(parts))
    for class_name in ("topichtml", "field-label", "qtypetip"):
        title_element = question_div.find(class_=class_name)
        if not title_element:
            continue
        text = title_element.get_text(" ", strip=True)
        text = _normalize_html_text(text)
        if text:
            return text
    blockquote = question_div.find("blockquote")
    if blockquote is not None:
        text = blockquote.get_text(" ", strip=True)
        text = _normalize_html_text(text)
        if text:
            return text
    text = question_div.get_text(" ", strip=True)
    return _normalize_html_text(text)


def _count_text_inputs_in_soup(question_div) -> int:
    candidates = question_div.find_all(["input", "textarea", "span", "div"])
    count = 0
    for cand in candidates:
        tag_name = (cand.name or "").lower()
        input_type = (cand.get("type") or "").lower()
        style_text = (cand.get("style") or "").lower()
        class_attr = cand.get("class") or []
        if isinstance(class_attr, str):
            class_text = class_attr.lower()
        else:
            class_text = " ".join(class_attr).lower()
        is_textcont = "textcont" in class_text or "textedit" in class_text

        if (
            input_type == "hidden"
            or "display:none" in style_text
            or "visibility:hidden" in style_text
        ):
            continue
        if tag_name == "input" and _input_looks_like_location(cand):
            continue

        if tag_name == "input":
            sibling = cand.find_next_sibling()
            sibling_classes = sibling.get("class") if sibling else None
            if sibling_classes and any("textedit" in cls.lower() for cls in sibling_classes):
                continue
        if tag_name == "textarea" or (
            tag_name == "input" and input_type in _TEXT_INPUT_ALLOWED_TYPES
        ):
            count += 1
            continue
        contenteditable = (cand.get("contenteditable") or "").lower() == "true"
        if (contenteditable or is_textcont) and tag_name in {"span", "div"}:
            count += 1
    return count


def _extract_text_input_labels(question_div) -> list[str]:

    labels = []

    def _label_before_node(node) -> str:
        parts: list[str] = []
        current = getattr(node, "previous_sibling", None)
        while current is not None:
            name = str(getattr(current, "name", "") or "").lower()
            if name in {"input", "textarea", "label", "span"}:
                if name == "input":
                    current = getattr(current, "previous_sibling", None)
                    continue
                break
            if name in {"br"}:
                break
            text = _normalize_html_text(
                current.get_text(" ", strip=True) if hasattr(current, "get_text") else str(current)
            )
            if text:
                parts.append(text)
            current = getattr(current, "previous_sibling", None)
        return _normalize_html_text(" ".join(reversed(parts))).rstrip("：:").strip()

    candidates = question_div.find_all(["input", "textarea", "span", "div"])

    for cand in candidates:
        tag_name = (cand.name or "").lower()
        input_type = (cand.get("type") or "").lower()
        style_text = (cand.get("style") or "").lower()
        class_attr = cand.get("class") or []
        class_text = (
            " ".join(class_attr).lower()
            if isinstance(class_attr, list)
            else str(class_attr).lower()
        )
        is_textcont = "textcont" in class_text or "textedit" in class_text

        if (
            input_type == "hidden"
            or "display:none" in style_text
            or "visibility:hidden" in style_text
        ):
            continue
        if tag_name == "input" and _input_looks_like_location(cand):
            continue

        if tag_name == "input":
            sibling = cand.find_next_sibling()
            if (
                sibling
                and sibling.get("class")
                and any("textedit" in cls.lower() for cls in sibling.get("class"))
            ):
                continue

        is_text_input = False
        if tag_name == "textarea" or (
            tag_name == "input" and input_type in _TEXT_INPUT_ALLOWED_TYPES
        ):
            is_text_input = True
        elif (cand.get("contenteditable") == "true" or is_textcont) and tag_name in {"span", "div"}:
            is_text_input = True

        if is_text_input:
            label = (
                cand.get("placeholder") or cand.get("aria-label") or cand.get("data-label") or ""
            )
            if not label:
                prev = cand.find_previous_sibling(string=True)
                if prev:
                    label = prev.strip().rstrip("：:").strip()
            if not label:
                label = _label_before_node(cand)
            if not label and is_textcont:
                parent = cand.find_parent()
                if parent is not None:
                    label = _label_before_node(parent)
            labels.append(label if label else f"填空{len(labels) + 1}")

    return labels


def _soup_question_looks_like_description(question_div, type_code: str) -> bool:

    if question_div is None:
        return False
    relation = str(question_div.get("relation") or "").strip()
    style_text = str(question_div.get("style") or "").lower()
    is_unreachable_placeholder = (
        relation == "-1"
        and "display:none" in style_text.replace(" ", "")
        and not _soup_question_is_required(question_div)
    )
    if is_unreachable_placeholder:
        return True

    if type_code not in {TypeCode.SINGLE, TypeCode.MULTIPLE}:
        return False

    choice_inputs = question_div.find_all(
        "input", attrs={"type": lambda v: v and v.lower() in ("radio", "checkbox")}
    )
    if choice_inputs:
        return False

    has_control_group = bool(question_div.select_one(".ui-controlgroup"))
    if has_control_group:
        return False

    has_jq_controls = bool(question_div.select_one(".jqradio, .jqcheck"))
    if has_jq_controls:
        return False

    return True


def _soup_question_looks_like_reorder(question_div) -> bool:

    if question_div is None:
        return False
    if question_div.select_one(".sortnum, .sortnum-sel, .order-number, .order-index"):
        return True
    has_list_items = bool(question_div.select("ul li, ol li"))
    if not has_list_items:
        return False
    has_sort_signature = bool(
        question_div.select(".ui-sortable, .ui-sortable-handle, [class*='sort']")
    )
    return has_sort_signature


def _soup_question_looks_like_numeric_scale(question_div) -> bool:

    if question_div is None:
        return False
    anchors = question_div.select("ul[tp='d'] li a, .scale-rating ul li a, .scale-rating a[val]")
    texts: list[str] = []
    for anchor in anchors:
        text = _normalize_html_text(anchor.get_text(" ", strip=True))
        if not text:
            text = _normalize_html_text(
                anchor.get("title")
                or anchor.get("aria-label")
                or anchor.get("val")
                or anchor.get("value")
                or anchor.get("dval")
                or anchor.get("data-value")
                or anchor.get("data-val")
                or ""
            )
        if text:
            texts.append(text)
    if not texts:
        return False
    numeric_count = sum(1 for t in texts if re.fullmatch(r"\d{1,2}", t))
    has_scale_title = bool(
        question_div.select_one(
            ".scaleTitle, .scaleTitle_frist, .scaleTitle_last, .scaleTitleFirst, .scaleTitleLast"
        )
    )
    total = len(texts)
    return (
        total >= 5 and numeric_count >= max(3, int(total * 0.7)) and (total >= 9 or has_scale_title)
    )


def _soup_question_looks_like_rating(question_div) -> bool:

    if question_div is None:
        return False

    if _soup_question_looks_like_numeric_scale(question_div):
        return False
    has_rate_icon = bool(question_div.select_one("a.rate-off, a.rate-on, .rate-off, .rate-on"))
    has_tag_wrap = bool(question_div.find(class_="evaluateTagWrap"))
    has_iconfont = bool(question_div.select_one(".scale-rating .iconfontNew, .iconfontNew"))

    if has_tag_wrap:
        return True
    if has_rate_icon or has_iconfont:
        return True
    return False


def _extract_rating_option_count(question_div) -> int:

    if question_div is None:
        return 0
    rating_list = question_div.find("ul", class_=WJX_MODLEN_CLASS_RE)
    if rating_list:
        class_attr = rating_list.get("class") or []
        for cls in class_attr:
            match = WJX_MODLEN_CLASS_RE.search(str(cls))
            if match:
                try:
                    return int(match.group("count"))
                except (ValueError, TypeError):
                    pass
    options = question_div.select(".scale-rating ul li")
    if options:
        return len(options)
    options = question_div.select("a.rate-off, a.rate-on")
    if options:
        return len(options)
    return 0


def _should_mark_as_multi_text(
    type_code: str | None,
    option_count: int,
    text_input_count: int,
    is_location: bool,
    has_gapfill: bool = False,
    has_slider_matrix: bool = False,
) -> bool:
    if is_location:
        return False
    if has_slider_matrix:
        return False
    normalized = _normalize_question_type_code(type_code)
    if normalized == TypeCode.MATRIX and has_gapfill:
        return True
    if text_input_count < 2:
        return False
    if normalized in {TypeCode.TEXT, TypeCode.LOCATION, TypeCode.MATRIX}:
        return True
    if normalized in _KNOWN_NON_TEXT_QUESTION_TYPES:
        return False
    if (option_count or 0) == 0:
        return True
    return (option_count or 0) <= 1 and text_input_count >= 2
