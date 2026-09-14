"""问卷星问卷 HTML 解析入口（lxml + XPath）。

整条链路是三段式流水线，每一段只做一件事：

1. :mod:`features` — **特征抽取**：对单个题目 div 做一轮 XPath，
   把结构标记、节点集合、文本输入、量表锚点等信号抽成 :class:`Features`；
2. :mod:`classify` — **题型分类**：纯函数，不再触碰 DOM，
   把信号翻译成题型代码与配套标记（描述题 / 量表 / 地区 / 高校 / 必答）；
3. :mod:`models` — **解析模型**：按题型分派，把 DOM 变成选项、行文本、
   可填空下标、多选限额。

本模块只负责编排三段 + 装配结果字段，对外暴露 ``parser.py`` 需要的三个符号。
"""

from __future__ import annotations

from lxml import html as lxml_html

from survey_submitter.core.questions.types import QuestionType
from survey_submitter.core.questions.utils import (
    _NON_TEXT_TYPES,
    _is_text_like_question,
    _normalize_question_type_code,
)
from survey_submitter.providers.contracts import LOGIC_PARSE_STATUS_NONE
from survey_submitter.providers.match_utils import normalize_match_text

from ..regexes import WJX_QUESTION_PREFIX_RE, WJX_TITLE_SUFFIX_RE

# 别名导入：``classify`` 这个名字留给子模块本身，避免被函数遮蔽
from .classify import classify as classify_question
from .features import Marker
from .features import extract as extract_features
from .logic import (
    display_conditions,
    finalize_logic_parse_status,
    jump_rules,
    propagate_cut_field,
)
from .models import (
    apply_rating_scale,
    attached_selects,
    forced_option,
    slider_range,
)
from .models import (
    parse as parse_options,
)
from .texts import _normalize_html_text, class_xpath, first, nodes, text_of

__all__ = [
    "_normalize_html_text",
    "extract_survey_title_from_html",
    "parse_survey_questions_from_html",
]

_ATTACHED_SELECT_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE})
_FORCED_OPTION_TYPES = frozenset(
    {
        QuestionType.SINGLE,
        QuestionType.SCORE,
        QuestionType.SCALE,
        QuestionType.DROPDOWN,
    }
)

_XP_CONTAINER = "//div[@id='divQuestion']"
_XP_IMAGES = ".//img"
_XP_TEXTAREA = ".//textarea"
_XP_TITLE_TAG = "//title"
_XP_HEADER = ("//h1", "//h2")

_TITLE_SELECTORS = (
    "//*[@id='divTitle']//h1",
    "//*[@id='divTitle']",
    *(
        f"//*[{class_xpath(name)}]"
        for name in (
            "surveytitle",
            "survey-title",
            "surveyTitle",
            "wjdcTitle",
            "htitle",
            "topic_tit",
        )
    ),
    "//*[@id='htitle']",
    "//*[@id='lbTitle']",
)


# --------------------------------------------------------------------------- 标题


def extract_survey_title_from_html(html: str) -> str | None:
    root = _parse_document(html)
    if root is None:
        return None

    candidates: list[str] = []
    for xpath in _TITLE_SELECTORS:
        element = first(root, xpath)
        if element is not None:
            text = _normalize_html_text(text_of(element))
            if text:
                candidates.append(text)

    if not candidates:
        for xpath in _XP_HEADER:
            header = first(root, xpath)
            if header is None:
                continue
            text = _normalize_html_text(text_of(header))
            if text:
                candidates.append(text)
                break

    title_element = first(root, _XP_TITLE_TAG)
    if title_element is not None:
        text = _normalize_html_text(text_of(title_element))
        if text:
            candidates.append(text)

    for raw in candidates:
        cleaned = WJX_TITLE_SUFFIX_RE.sub("", raw).strip(" -_|")
        if cleaned:
            return cleaned
    return None


def _parse_document(html: str):
    if not html:
        return None
    try:
        return lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001 —— 外部 HTML 不可信，解析失败一律降级为 None
        return None


# --------------------------------------------------------------------------- 编号


def _display_question_number(raw_heading_text: str | None) -> int | None:
    text = normalize_match_text(raw_heading_text)
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


def _has_question_ancestor(question_div, fieldset) -> bool:
    current = question_div.getparent()
    while current is not None and current is not fieldset:
        if current.tag == "div" and current.get("topic") is not None:
            return True
        current = current.getparent()
    return False


def _resolve_display_number(
    features,
    current_display_num: int | None,
    visible_question_counter: int,
) -> tuple[int | None, int | None, int]:
    display_num = _display_question_number(features.heading_text)
    if display_num is None:
        display_num = current_display_num
    elif display_num > 0:
        current_display_num = display_num
    if Marker.HIDDEN not in features.markers:
        visible_question_counter += 1
        if display_num is None or display_num != visible_question_counter:
            display_num = visible_question_counter
            current_display_num = display_num
    return display_num, current_display_num, visible_question_counter


# --------------------------------------------------------------------------- 标记


def _should_mark_as_multi_text(
    type_code: str | None,
    option_count: int,
    text_input_count: int,
    markers: frozenset[Marker],
) -> bool:
    if Marker.LOCATION in markers or Marker.SLIDER_MATRIX in markers:
        return False
    normalized = _normalize_question_type_code(type_code)
    if normalized == QuestionType.MATRIX and Marker.GAP_FILL in markers:
        return True
    if text_input_count < 2:
        return False
    if normalized in {QuestionType.TEXT, QuestionType.LOCATION, QuestionType.MATRIX}:
        return True
    if normalized in _NON_TEXT_TYPES:
        return False
    if (option_count or 0) == 0:
        return True
    return (option_count or 0) <= 1


# --------------------------------------------------------------------------- 媒体


def _normalize_media_source_url(raw: str | None) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    return text


def _append_media_item(
    media: list[dict[str, object]],
    *,
    scope: str,
    index: int | None,
    source_url: str | None,
    label: str,
) -> None:
    normalized_url = _normalize_media_source_url(source_url)
    if not normalized_url:
        return
    item: dict[str, object] = {
        "kind": "image",
        "scope": scope,
        "index": index,
        "source_url": normalized_url,
        "label": str(label or "").strip(),
    }
    if item not in media:
        media.append(item)


def _append_media_images(media, scope_nodes, *, scope, texts, default_label, indexed=True) -> None:
    for position, node in enumerate(scope_nodes):
        label = str(texts[position] or "").strip() if position < len(texts) else ""
        for image in nodes(node, _XP_IMAGES):
            _append_media_item(
                media,
                scope=scope,
                index=position if indexed else None,
                source_url=image.get("src") or image.get("data-src") or image.get("data-original"),
                label=label or default_label(position),
            )


def _collect_question_media(features, row_texts: list[str], option_texts: list[str]) -> list[dict]:
    node = features.node
    if node is None:
        return []
    media: list[dict] = []

    title_nodes = [
        element
        for xpath in (f".//*[{class_xpath('topichtml')}]", f".//*[{class_xpath('field-label')}]")
        for element in nodes(node, xpath)
    ]
    _append_media_images(
        media,
        title_nodes,
        scope="title",
        texts=[],
        default_label=lambda _position: "题干图",
        indexed=False,
    )

    _append_media_images(
        media,
        features.option_nodes,
        scope="option",
        texts=option_texts,
        default_label=lambda position: f"选项 {position + 1}",
    )

    row_nodes: list = []
    for xpath in (
        ".//tr[@rowindex]",
        f".//tr[{class_xpath('rowtitletr')}]",
        ".//tr[starts-with(@id,'drv')]",
    ):
        row_nodes = nodes(node, xpath)
        if row_nodes:
            break
    _append_media_images(
        media,
        row_nodes,
        scope="row",
        texts=row_texts,
        default_label=lambda position: f"第 {position + 1} 行",
    )
    return media


# --------------------------------------------------------------------------- 单题


def _process(features, page_index: int, current_display_num: int | None, visible_counter: int):
    """编排三段流水线，返回 (题目字典, 最新显示号, 可见计数)。"""
    if features.number is None:
        heading_num = _display_question_number(features.heading_text)
        if heading_num is not None:
            current_display_num = heading_num
        return None, current_display_num, visible_counter

    classification = classify_question(features)
    type_code = classification.type_code
    markers = features.markers

    options = parse_options(features, type_code)
    option_texts, option_count = apply_rating_scale(
        features.node,
        type_code,
        options.option_texts,
        options.option_count,
        "rating" in classification.hits,
        classification.rating_max,
    )
    if "description" in classification.hits:
        type_code = QuestionType.DESCRIPTION

    display_num, current_display_num, visible_counter = _resolve_display_number(
        features, current_display_num, visible_counter
    )

    slider_min, slider_max, slider_step = (None, None, None)
    if type_code == QuestionType.SLIDER or Marker.SLIDER_MATRIX in markers:
        slider_min, slider_max, slider_step = slider_range(features)

    multi_text = _should_mark_as_multi_text(
        type_code,
        option_count,
        features.text_input_count,
        markers,
    )
    if multi_text and type_code == QuestionType.MATRIX:
        type_code = QuestionType.MULTI_TEXT

    attached = attached_selects(features) if type_code in _ATTACHED_SELECT_TYPES else []
    forced_index, forced_text = (None, None)
    if type_code in _FORCED_OPTION_TYPES:
        forced_index, forced_text = forced_option(features, features.title_text, option_texts)
    jump_hit, jump_rule_list = jump_rules(features, option_texts)
    condition_hit, conditions = display_conditions(features.relation)

    return (
        {
            "num": features.number,
            "display_num": display_num,
            "title": features.title_text,
            "type_code": type_code,
            "options": option_count,
            "rows": options.matrix_rows,
            "row_texts": options.row_texts,
            "page": page_index,
            "option_texts": option_texts,
            "forced_option_index": forced_index,
            "forced_option_text": forced_text,
            "fillable_options": options.fillable_indices,
            "required_fillable_options": options.required_fillable_indices,
            "attached_option_selects": attached,
            "has_attached_option_select": bool(attached),
            "is_location": Marker.LOCATION in markers,
            "location_verify_type": classification.location_verify_type,
            "is_rating": "rating" in classification.hits,
            "is_description": "description" in classification.hits,
            "rating_max": classification.rating_max,
            "text_inputs": features.text_input_count,
            "text_input_labels": features.text_input_labels,
            "is_multi_text": multi_text,
            # core 侧签名仍是布尔开关（core.questions.utils），此处只传值
            "is_text_like": _is_text_like_question(
                type_code,
                option_count,
                features.text_input_count,
                has_slider_matrix=Marker.SLIDER_MATRIX in markers,
                is_location=Marker.LOCATION in markers,
            ),
            "is_slider_matrix": Marker.SLIDER_MATRIX in markers,
            "has_jump": jump_hit,
            "jump_rules": jump_rule_list,
            "has_display_condition": condition_hit,
            "display_conditions": conditions,
            "logic_parse_status": LOGIC_PARSE_STATUS_NONE,
            "question_media": _collect_question_media(features, options.row_texts, option_texts),
            "slider_min": slider_min,
            "slider_max": slider_max,
            "slider_step": slider_step,
            "multi_min_limit": options.multi_min_limit,
            "multi_max_limit": options.multi_max_limit,
            "required": Marker.REQUIRED in markers,
        },
        current_display_num,
        visible_counter,
    )


# --------------------------------------------------------------------------- 入口


def parse_survey_questions_from_html(html: str) -> list[dict[str, object]]:
    root = _parse_document(html)
    if root is None:
        return []
    container = first(root, _XP_CONTAINER)
    if container is None:
        return []

    fieldsets = nodes(container, ".//fieldset") or [container]

    questions_info: list[dict[str, object]] = []
    for page_index, fieldset in enumerate(fieldsets, 1):
        current_display_num: int | None = None
        visible_counter = 0
        for question_div in nodes(fieldset, ".//div[@topic]"):
            if _has_question_ancestor(question_div, fieldset):
                continue
            question_info, current_display_num, visible_counter = _process(
                extract_features(question_div, root),
                page_index,
                current_display_num,
                visible_counter,
            )
            if question_info is not None:
                questions_info.append(question_info)

    propagate_cut_field(container, questions_info)
    finalize_logic_parse_status(questions_info)
    return questions_info
