"""问卷星 HTML 解析器各阶段单元测试（lxml + XPath 实现）。

覆盖三段流水线：``texts`` / ``features``（特征抽取）、``classify``（题型分类）、
``models`` / ``logic``（解析模型与逻辑）。
"""

from __future__ import annotations

from lxml import html as lxml_html

from survey_submitter.core.questions.types import QuestionType
from survey_submitter.providers.wjx.html_parser import (
    _display_question_number,
    _should_mark_as_multi_text,
    extract_survey_title_from_html,
)
from survey_submitter.providers.wjx.html_parser import classify as classify_module
from survey_submitter.providers.wjx.html_parser import features as features_module
from survey_submitter.providers.wjx.html_parser import logic as logic_module
from survey_submitter.providers.wjx.html_parser import models as models_module
from survey_submitter.providers.wjx.html_parser import texts as texts_module
from survey_submitter.providers.wjx.html_parser.features import Marker


def _node(html: str):
    """把 HTML 片段解析成片段的根元素。"""
    return lxml_html.fromstring(html.strip())


def _features(html: str):
    node = _node(html)
    return features_module.extract(node, node)


def _rule(name: str) -> classify_module.Rule:
    return next(rule for rule in classify_module.RULES if rule.name == name)


class WjxHtmlParserHelperTests:
    def test_force_select_text_helpers_and_fragment_dedupe(self) -> None:
        features = _features(
            """
            <div>
              <div class="topichtml">请务必选 A 项</div>
              <div class="field-label">请务必选 A 项</div>
            </div>
            """
        )

        assert models_module._normalize_force_text(" 【A】 选项 ") == "a选项"
        assert models_module._force_option_label("(B) 香蕉") == "B"
        assert models_module._force_option_label("普通文本") is None
        assert models_module._force_fragments(features, "请务必选 A 项") == ["请务必选 A 项"]

    def test_text_input_helpers_detect_shared_other_inputs(self) -> None:
        ui_other_div = _node("<div class='ui-other'><input type='text' /></div>")
        keyword_div = _node("<div><input id='other_reason' type='text' /></div>")

        assert texts_module.text_input_control(_node("<textarea></textarea>"))
        assert texts_module.text_input_control(_node("<input type='text' />"))
        assert not texts_module.text_input_control(_node("<input type='radio' />"))
        assert models_module._has_text_input(ui_other_div)
        assert models_module._has_shared_text_input(ui_other_div)
        assert models_module._has_shared_text_input(keyword_div)

    def test_extract_option_text_from_attrs_prefers_primary_and_child_attrs(self) -> None:
        primary = _node("<div title='主标题'></div>")
        child = _node("<div><span aria-label='子标题'></span></div>")
        fallback = _node("<div data-val='备用值'></div>")

        assert texts_module.option_text_from_attrs(primary) == "主标题"
        assert texts_module.option_text_from_attrs(child) == "子标题"
        assert texts_module.option_text_from_attrs(fallback) == "备用值"
        assert texts_module.option_text_from_attrs(None) == ""

    def test_extract_rating_option_texts_can_fall_back_to_numbering(self) -> None:
        rating_div = _node(
            """
            <div>
              <ul class="modlen3">
                <li><a class="rate-off"></a></li>
                <li><a class="rate-off" val="2"></a></li>
                <li><a class="rate-off"></a></li>
              </ul>
            </div>
            """
        )

        assert models_module._rating_texts(rating_div) == ["1", "2", "3"]
        assert texts_module.has_content("A1")
        assert not texts_module.has_content("   ")

    def test_extract_survey_title_from_html_strips_wjx_suffix(self) -> None:
        html = """
        <html>
          <head><title>备用标题 ｜ 问卷星</title></head>
          <body><div id="divTitle"><h1>正式标题 ｜ 问卷星</h1></div></body>
        </html>
        """
        assert extract_survey_title_from_html(html) == "正式标题"

    def test_extract_question_number_and_cleanup_helpers(self) -> None:
        assert features_module._question_number(_node("<div id='div12' topic='12'></div>")) == 12
        assert features_module._question_number(_node("<div id='div77'></div>")) == 77
        assert features_module._cleanup_title(" １． 【单选题】 题目标题 ") == "题目标题"
        assert features_module._cleanup_title(" 第1题 【多选题】 题目标题 ") == "题目标题"
        assert features_module._cleanup_title(" Q1 题目标题 ") == "题目标题"
        assert _display_question_number("* 18. 题目") == 18
        assert _display_question_number("第8题 题目") == 8
        assert _display_question_number("Q9 题目") == 9
        assert _display_question_number("10、题目") == 10

    def test_extract_display_heading_text_includes_split_topic_number(self) -> None:
        node = _node(
            """
            <div id="div23" topic="23" type="2">
              <div class="field-label">
                <span class="req">*</span>
                <div class="topicnumber">22.</div>
                <div class="topichtml">请评价培训和实习</div>
              </div>
            </div>
            """
        )
        heading = features_module._heading_text(node)
        assert heading == "22. 请评价培训和实习"
        assert _display_question_number(heading) == 22

    def test_count_text_inputs_and_extract_labels_from_mixed_nodes(self) -> None:
        node = _node(
            """
            <div>
              <input type="text" placeholder="姓名" />
              性别：<input type="text" />
              <textarea aria-label="备注"></textarea>
              <div contenteditable="true"></div>
              <input type="hidden" />
              <input type="text" /><span class="textedit"></span>
            </div>
            """
        )
        count, text_nodes = features_module._collect_text_inputs(node)
        assert count == 5
        assert features_module._text_input_labels(text_nodes) == [
            "姓名",
            "性别",
            "备注",
            "填空4",
            "填空5",
        ]

    def test_extract_display_heading_text_falls_back_to_blockquote_and_plain_text(self) -> None:
        blockquote_div = _node("<div><blockquote> 引用标题 </blockquote></div>")
        plain_div = _node("<div> 普通标题 </div>")

        assert features_module._heading_text(blockquote_div) == "引用标题"
        assert features_module._heading_text(plain_div) == "普通标题"

    def test_description_reorder_scale_and_rating_detection(self) -> None:
        description_features = _features("<div><div class='topichtml'>说明</div></div>")
        reorder_div = _node(
            "<div><ul><li>A</li><li>B</li></ul><div class='ui-sortable'></div></div>"
        )
        scale_div = _node(
            """
            <div>
              <div class="scaleTitle"></div>
              <ul tp="d">
                <li><a>1</a></li><li><a>2</a></li><li><a>3</a></li><li><a>4</a></li><li><a>5</a></li>
                <li><a>6</a></li><li><a>7</a></li><li><a>8</a></li><li><a>9</a></li><li><a>10</a></li>
              </ul>
            </div>
            """
        )
        rating_div = _node(
            "<div><div class='evaluateTagWrap'></div><a class='rate-off'>星</a></div>"
        )
        rating_count_div = _node("<div><ul class='modlen5'><li></li></ul></div>")

        description_rule = _rule("description")
        assert description_rule.matches(description_features, QuestionType.SINGLE)
        assert not description_rule.matches(
            _features("<div><input type='radio'/></div>"), QuestionType.SINGLE
        )
        assert features_module._reorder(reorder_div)
        assert features_module._numeric_scale(scale_div)
        assert not features_module._rating(scale_div)
        assert features_module._rating(rating_div)
        assert features_module._rating_max(rating_count_div) == 5

    def test_dval_scale_with_blank_rate_icons_is_not_rating(self) -> None:
        scale_div = _node(
            """
            <div>
              <div class="scaleTitle_frist">很不同意</div>
              <div class="scaleTitle_last">很同意</div>
              <ul tp="d">
                <li><a class="rate-off" dval="1"></a></li>
                <li><a class="rate-off" dval="2"></a></li>
                <li><a class="rate-off" dval="3"></a></li>
                <li><a class="rate-off" dval="4"></a></li>
                <li><a class="rate-off" dval="5"></a></li>
              </ul>
            </div>
            """
        )

        assert features_module._numeric_scale(scale_div)
        assert not features_module._rating(scale_div)

    def test_required_and_select_placeholder_helpers(self) -> None:
        required_cases = (
            "<div req='1'><div class='topichtml'>题目</div></div>",
            "<div><div class='topichtml'>* 必答题</div></div>",
            "<div><span class='required'></span></div>",
        )
        for html in required_cases:
            assert Marker.REQUIRED in _features(html).markers
        assert features_module.placeholder_option(0, "", "请选择")
        assert not features_module.placeholder_option(1, "1", "北京")

    def test_should_mark_as_multi_text_respects_type_and_flags(self) -> None:
        no_markers = frozenset()
        assert _should_mark_as_multi_text("text", 0, 2, no_markers)
        assert _should_mark_as_multi_text("matrix", 0, 1, frozenset({Marker.GAP_FILL}))
        assert not _should_mark_as_multi_text("single", 4, 2, no_markers)
        assert not _should_mark_as_multi_text("text", 0, 2, frozenset({Marker.LOCATION}))
        assert not _should_mark_as_multi_text("text", 0, 2, frozenset({Marker.SLIDER_MATRIX}))

    def test_force_select_detection_supports_text_label_and_index(self) -> None:
        text_features = _features(
            "<div><div class='topichtml'>本题检测，请选择 非常满意。</div></div>"
        )
        label_features = _features("<div><div class='topichtml'>请务必选A项</div></div>")
        index_features = _features("<div><div class='topichtml'>请直接选第2项</div></div>")
        detached = features_module.Features(node=None, root=None)

        assert models_module.forced_option(
            text_features, "本题检测，请选择 非常满意。", ["非常不满意", "非常满意"]
        ) == (1, "非常满意")
        assert models_module.forced_option(
            label_features, "请务必选A项", ["(A) 苹果", "(B) 香蕉"]
        ) == (0, "(A) 苹果")
        assert models_module.forced_option(index_features, "请直接选第2项", ["甲", "乙", "丙"]) == (
            1,
            "乙",
        )
        assert models_module.forced_option(detached, "请直接选第9项", ["甲", "乙"]) == (
            None,
            None,
        )

    def test_force_select_text_matching_requires_exact_normalized_text(self) -> None:
        features = _features("<div><div class='topichtml'>请直接选满意</div></div>")
        assert models_module.forced_option(features, "请直接选满意", ["不满意", "满意度一般"]) == (
            None,
            None,
        )
        assert models_module.forced_option(features, "请直接选满意", ["数字1", "满意"]) == (
            1,
            "满意",
        )

    def test_choice_option_and_attached_select_parsing_marks_fillable_options(self) -> None:
        features = _features(
            """
            <div>
              <div class="ui-controlgroup">
                <div>
                  <span class="label">选项A</span>
                </div>
                <div>
                  <span class="label">其他</span>
                  <input type="text" />
                  <select>
                    <option value="">请选择</option>
                    <option>红色</option>
                    <option>蓝色</option>
                  </select>
                </div>
              </div>
            </div>
            """
        )

        options = models_module._choice_model(features, QuestionType.MULTIPLE)
        attached = models_module.attached_selects(features)

        assert options.option_texts == ["选项A", "其他"]
        assert options.fillable_indices == [1]
        assert options.required_fillable_indices == []
        assert attached == [
            {
                "option_index": 1,
                "option_text": "其他",
                "select_options": ["红色", "蓝色"],
                "select_option_count": 2,
            }
        ]

    def test_choice_option_parsing_falls_back_to_plain_list_and_shared_input(self) -> None:
        features = _features(
            """
            <div>
              <ul>
                <li>选项一</li>
                <li>选项二</li>
              </ul>
              <div class="ui-other"><input type="text" /></div>
            </div>
            """
        )

        options = models_module._choice_model(features, QuestionType.MULTIPLE)
        assert options.option_texts == ["选项一", "选项二"]
        assert options.fillable_indices == [1]
        assert options.required_fillable_indices == []

    def test_custom_select_and_location_helpers(self) -> None:
        custom_input = _node("<input custom='请选择, 苹果,香蕉, 苹果' />")
        typo_custom_input = _node("<input cusom='北京|上海|北京' />")
        location_input = (
            "<input type='text' verify='省市区' onclick='openCityBox(this,3,event,1);'"
            " readonly='readonly' />"
        )
        select_features = _features(
            """
            <div>
              <div topic="7">
                <select id="q7">
                  <option value="">请选择</option>
                  <option value="1">北京</option>
                  <option value="2">上海</option>
                </select>
              </div>
            </div>
            """
        )

        assert models_module._custom_select_texts(custom_input) == ["苹果", "香蕉"]
        assert models_module._custom_select_texts(typo_custom_input) == ["北京", "上海"]
        assert features_module._location_verify_hit("腾讯地图")
        assert features_module._location_verify_hit("省市区")
        assert not features_module._location_verify_hit("city")
        assert not features_module._location_verify_hit("province")
        assert not features_module._location_verify_hit("area")
        assert not features_module._location_verify_hit("普通文本")
        assert Marker.LOCATION in _features("<div><input verify='地图定位' /></div>").markers
        assert (
            Marker.LOCATION
            in _features(
                "<div><input verify='省市区' onclick='openCityBox(this,3,event,1);' /></div>"
            ).markers
        )
        assert (
            Marker.LOCATION
            not in _features("<div><input onclick='openCityBox(this,3,event,1);' /></div>").markers
        )
        assert Marker.LOCATION not in _features("<div><input verify='city' /></div>").markers
        assert features_module._collect_text_inputs(_node(f"<div>{location_input}</div>"))[0] == 0
        assert models_module._dropdown_model(select_features).option_texts == ["北京", "上海"]
        assert models_module._select_option_texts(select_features.node.xpath(".//select")[0]) == [
            "北京",
            "上海",
        ]

    def test_question_title_limits_jump_and_display_rules(self) -> None:
        features = _features(
            """
            <div relation="1,1|1,1|3,1,2">
              <div class="topichtml">2. 请选择你喜欢的项目 [至少选2项，最多选4项]</div>
              <input type="checkbox" jumpto="5" />
              <input type="checkbox" />
            </div>
            """
        )

        assert features.title_text == "请选择你喜欢的项目 [至少选2项，最多选4项]"
        assert models_module._multiple_limits(features.node) == (2, 4)
        assert logic_module.jump_rules(features, ["A", "B"]) == (
            True,
            [{"option_index": 0, "jumpto": 5, "option_text": "A", "terminates_survey": False}],
        )
        assert logic_module.display_conditions(features.relation) == (
            True,
            [
                {
                    "condition_question_num": 1,
                    "condition_mode": "selected",
                    "condition_option_indices": [0],
                    "raw_relation": "1,1",
                },
                {
                    "condition_question_num": 3,
                    "condition_mode": "selected",
                    "condition_option_indices": [0, 1],
                    "raw_relation": "3,1,2",
                },
            ],
        )

    def test_attach_display_condition_metadata_marks_source_question(self) -> None:
        questions: list[dict[str, object]] = [
            {"num": 1, "display_conditions": [], "controls_display_targets": []},
            {
                "num": 2,
                "display_conditions": [
                    {
                        "condition_question_num": 1,
                        "condition_mode": "selected",
                        "condition_option_indices": [1],
                    }
                ],
                "controls_display_targets": [],
            },
        ]

        logic_module.attach_display_condition_metadata(questions)

        assert questions[0]["has_dependent_display_logic"] is True
        assert questions[0]["controls_display_targets"] == [
            {
                "target_question_num": 2,
                "condition_option_indices": [1],
                "condition_mode": "selected",
            }
        ]

    def test_multi_limit_fragment_collection_and_metadata_helpers(self) -> None:
        features = _features(
            """
            <div topic="7" type="7">
              <div class="topichtml">至少选1项，最多选3项</div>
              <ul><li>选项1</li><li>选项2</li></ul>
              <select><option value="">请选择</option><option>北京</option></select>
              <input id="other_city" type="text" />
            </div>
            """
        )

        assert models_module._limit_fragments(features.node) == ["至少选1项，最多选3项"]
        options = models_module.parse(features, QuestionType.DROPDOWN)
        assert options.option_texts == ["北京"]
        assert options.option_count == 1
        assert options.fillable_indices == [0]

    def test_choice_metadata_marks_required_fillable_option_indices(self) -> None:
        features = _features(
            """
            <div topic="7" type="4">
              <div class="ui-controlgroup">
                <div>
                  <span class="label">选项A</span>
                  <div class="ui-text"><input class="OtherText" type="text" /></div>
                </div>
                <div>
                  <span class="label">选项B</span>
                  <div class="ui-text"><input class="OtherText" type="text" required="required" /></div>
                </div>
                <div>
                  <span class="label">选项C</span>
                </div>
              </div>
            </div>
            """
        )

        options = models_module.parse(features, QuestionType.MULTIPLE)
        assert options.fillable_indices == [0, 1]
        assert options.required_fillable_indices == [1]

    def test_choice_metadata_marks_required_shared_other_input(self) -> None:
        features = _features(
            """
            <div topic="7" type="4">
              <ul>
                <li>选项一</li>
                <li>选项二</li>
              </ul>
              <div class="ui-other">
                <input class="OtherText" type="text" required="required" />
              </div>
            </div>
            """
        )

        options = models_module.parse(features, QuestionType.MULTIPLE)
        assert options.fillable_indices == [1]
        assert options.required_fillable_indices == [1]

    def test_jump_and_display_rule_helpers_ignore_invalid_values(self) -> None:
        features = _features(
            """
            <div relation="bad|1,a|2,0|3,2,2">
              <input type="checkbox" data-jumpto="跳到第8题" />
              <input type="text" jumpto="9" />
            </div>
            """
        )

        has_jump, jump_rules = logic_module.jump_rules(features, ["A", "B"])
        has_display, display_rules = logic_module.display_conditions(features.relation)

        assert has_jump is True
        assert jump_rules == [
            {"option_index": 0, "jumpto": 8, "option_text": "A", "terminates_survey": False}
        ]
        assert has_display is True
        assert display_rules == [
            {
                "condition_question_num": 3,
                "condition_mode": "selected",
                "condition_option_indices": [1],
                "raw_relation": "3,2,2",
            }
        ]

    def test_jump_rule_helper_rejects_undefined_text_format(self) -> None:
        features = _features(
            """
            <div hasjump="1" type="3">
              <input type="radio" data-jumpto="去第8题" />
            </div>
            """
        )

        assert logic_module.jump_rules(features, ["A"]) == (True, [])

    def test_display_rule_helper_skips_dirty_chunks_and_keeps_valid_blocks(self) -> None:
        assert logic_module.display_conditions("1,1|脏数据|3,2,3|4,a,1|5,1") == (
            True,
            [
                {
                    "condition_question_num": 1,
                    "condition_mode": "selected",
                    "condition_option_indices": [0],
                    "raw_relation": "1,1",
                },
                {
                    "condition_question_num": 3,
                    "condition_mode": "selected",
                    "condition_option_indices": [1, 2],
                    "raw_relation": "3,2,3",
                },
                {
                    "condition_question_num": 5,
                    "condition_mode": "selected",
                    "condition_option_indices": [0],
                    "raw_relation": "5,1",
                },
            ],
        )

    def test_jump_rule_helper_supports_select_option_jumps(self) -> None:
        features = _features(
            """
            <div hasjump="1" type="7">
              <select>
                <option value="">请选择</option>
                <option value="1" jumpto="9">北京</option>
                <option value="2">上海</option>
              </select>
            </div>
            """
        )

        assert logic_module.jump_rules(features, ["北京", "上海"]) == (
            True,
            [{"option_index": 0, "jumpto": 9, "option_text": "北京", "terminates_survey": False}],
        )

    def test_jump_rule_helper_supports_unconditional_question_jump(self) -> None:
        features = _features(
            """
            <div hasjump="1" jumpto="11" type="3">
              <input type="radio" />
              <input type="radio" />
            </div>
            """
        )

        assert logic_module.jump_rules(features, ["A", "B"]) == (
            True,
            [{"option_index": -1, "jumpto": 11, "option_text": None}],
        )

    def test_jump_rule_helper_marks_wjx_end_option_as_terminate(self) -> None:
        features = _features(
            """
            <div hasjump="1" type="3">
              <input type="radio" value="1" />
              <input type="radio" value="2" jumpto="1" />
            </div>
            """
        )

        assert logic_module.jump_rules(features, ["有（继续作答）", "没有（结束作答）"]) == (
            True,
            [
                {
                    "option_index": 1,
                    "jumpto": 1,
                    "option_text": "没有（结束作答）",
                    "terminates_survey": True,
                }
            ],
        )

    def test_jump_rule_helper_treats_wjx_mobile_jumpto_one_as_terminate_without_keyword(
        self,
    ) -> None:
        features = _features(
            """
            <div hasjump="1" type="3">
              <input type="radio" value="1" />
              <input type="radio" value="2" jumpto="1" />
            </div>
            """
        )

        assert logic_module.jump_rules(
            features, ["是，应届毕业生", "否，暂时不是应届毕业生或准毕业生"]
        ) == (
            True,
            [
                {
                    "option_index": 1,
                    "jumpto": 1,
                    "option_text": "否，暂时不是应届毕业生或准毕业生",
                    "terminates_survey": True,
                }
            ],
        )

    def test_attach_display_condition_metadata_dedupes_and_clears_empty_targets(self) -> None:
        questions: list[dict[str, object]] = [
            {"num": 1, "display_conditions": [], "controls_display_targets": []},
            {
                "num": 2,
                "display_conditions": [
                    {
                        "condition_question_num": 1,
                        "condition_mode": "selected",
                        "condition_option_indices": [0, 0],
                    },
                    {
                        "condition_question_num": 1,
                        "condition_mode": "selected",
                        "condition_option_indices": [0],
                    },
                ],
                "controls_display_targets": [],
            },
            {"num": 3, "display_conditions": "bad", "controls_display_targets": []},
        ]

        logic_module.attach_display_condition_metadata(questions)

        assert questions[0]["controls_display_targets"] == [
            {
                "target_question_num": 2,
                "condition_option_indices": [0],
                "condition_mode": "selected",
            }
        ]
        assert questions[2]["controls_display_targets"] == []
        assert questions[2]["has_dependent_display_logic"] is False
