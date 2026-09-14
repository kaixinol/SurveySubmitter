"""阶段二：题型分类。

纯函数：拿阶段一抽好的 :class:`Features`，不再触碰 DOM，
按 :data:`RULES` 这张规则表把标记集合翻译成题型代码与配套标记。

规则表按声明顺序逐条匹配，命中的规则改写题型代码；
``type_code=None`` 的规则只登记命中事实（例如“说明段落”），不改题型。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from survey_submitter.core.questions.types import QuestionType, convert_wire_type_code

from ..university_list import UniversityList
from .features import Features, Marker
from .texts import first

_UNREACHABLE_RELATION = "-1"
_CHOICE_TYPES = frozenset({QuestionType.SINGLE, QuestionType.MULTIPLE})
# 说明段落不该出现的“可点控件”标记：命中其一就不是纯说明
_INTERACTIVE_MARKERS = frozenset({Marker.CHOICE_INPUTS, Marker.CONTROL_GROUP, Marker.JQ_CONTROLS})


@dataclass(frozen=True, slots=True)
class Rule:
    """题型纠正规则。

    Attributes:
        name: 规则名，命中后记进 :class:`Classification` 的 ``hits``。
        matches: 判定函数，接收特征与当前题型代码。
        type_code: 命中后改写成的题型；``None`` 表示只登记不改写。
        terminal: 命中后终止后续规则，用于表达互斥分支
            （对应旧实现 ``if / elif`` 只选一支的语义）。
    """

    name: str
    matches: Callable[[Features, str], bool]
    type_code: str | None = None
    terminal: bool = False


def _unreachable_placeholder(features: Features, _type_code: str) -> bool:
    """relation=-1 + 自身隐藏 + 非必答：问卷星拿来当不可达占位题。"""
    return (
        features.relation == _UNREACHABLE_RELATION
        and "display:none" in features.style.lower().replace(" ", "")
        and Marker.REQUIRED not in features.markers
    )


def _choiceless_choice(features: Features, type_code: str) -> bool:
    """没有可点控件的单选 / 多选题只是说明段落。"""
    return type_code in _CHOICE_TYPES and not features.markers & _INTERACTIVE_MARKERS


RULES: tuple[Rule, ...] = (
    Rule(
        "reorder",
        lambda f, t: t != QuestionType.ORDER and Marker.REORDER in f.markers,
        QuestionType.ORDER,
    ),
    Rule(
        "description",
        lambda f, t: _unreachable_placeholder(f, t) or _choiceless_choice(f, t),
    ),
    Rule(
        "rating",
        lambda f, t: t == QuestionType.SCORE and Marker.RATING in f.markers,
        QuestionType.SCALE,
    ),
    # 高校输入有时被标成普通选项题，get_Local / verify 属性才是可靠标记
    # 高校 / 地区 / 降级三支互斥，命中高校即短路
    Rule(
        "university",
        lambda f, t: (
            Marker.LOCATION in f.markers and UniversityList.is_university_verify(f.location_verify)
        ),
        QuestionType.UNIVERSITY,
        terminal=True,
    ),
    Rule("location", lambda f, t: Marker.LOCATION in f.markers, QuestionType.LOCATION),
    Rule(
        "demote_location",
        lambda f, t: (
            t == QuestionType.LOCATION
            and Marker.LOCATION not in f.markers
            and first(f.node, ".//textarea") is not None
        ),
        QuestionType.TEXT,
    ),
)


@dataclass(slots=True)
class Classification:
    """分类结果。

    布尔事实不再落成 ``is_xxx`` 字段：规则命中读 :attr:`hits`，
    信号标记读 ``features.markers``（装配层自行派生）。
    """

    type_code: str
    hits: frozenset[str]
    rating_max: int
    location_verify_type: str


def classify(features: Features) -> Classification:
    type_code = convert_wire_type_code(features.raw_type_code)
    fired: set[str] = set()
    for rule in RULES:
        if not rule.matches(features, type_code):
            continue
        fired.add(rule.name)
        if rule.type_code is not None:
            type_code = rule.type_code
        if rule.terminal:
            break

    hits = frozenset(fired)
    location_verify = features.location_verify if Marker.LOCATION in features.markers else ""
    return Classification(
        type_code=type_code,
        hits=hits,
        rating_max=features.rating_max if "rating" in hits else 0,
        location_verify_type=location_verify,
    )


__all__ = ["RULES", "Classification", "Rule", "classify"]
