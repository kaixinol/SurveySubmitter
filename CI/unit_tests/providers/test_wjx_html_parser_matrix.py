"""矩阵题 / 滑块题解析模型单元测试（lxml + XPath 实现）。"""

from __future__ import annotations

from lxml import html as lxml_html

from survey_submitter.providers.wjx.html_parser import features as features_module
from survey_submitter.providers.wjx.html_parser import models as models_module
from survey_submitter.providers.wjx.html_parser.features import Marker


def _node(html: str):
    """把 HTML 片段解析成片段的根元素。"""
    return lxml_html.fromstring(html.strip())


def _features(html: str):
    node = _node(html)
    return features_module.extract(node, node)


class WjxHtmlParserMatrixTests:
    def test_dedupe_matrix_option_texts_normalizes_and_drops_repeats(self) -> None:
        assert models_module._dedupe(["  好  ", "", "好", "一般"]) == ["好", "一般"]

    def test_collect_matrix_option_texts_prefers_table_rowindex_and_header(self) -> None:
        html = """
        <div id="div3">
          <table id="divRefTab3">
            <tr id="drv3_1"><td></td><td>差</td><td>好</td></tr>
            <tr rowindex="1"><td>外观</td><td></td><td></td></tr>
            <tr rowindex="2"><td data-title="功能"></td><td></td><td></td></tr>
          </table>
        </div>
        """
        options = models_module._matrix_model(_features(html))

        assert options.matrix_rows == 2
        assert options.option_texts == ["差", "好"]
        assert options.row_texts == ["外观", "功能"]

    def test_collect_matrix_option_texts_can_fall_back_to_input_names_and_item_titles(
        self,
    ) -> None:
        html = """
        <div id="div5">
          <span class="itemTitleSpan">行一</span>
          <span class="itemTitleSpan">行二</span>
          <input name="q5_1_1" />
          <input name="q5_1_2" />
          <input name="q5_2_1" />
          <input name="q5_2_2" />
        </div>
        """
        options = models_module._matrix_model(_features(html))

        assert options.matrix_rows == 2
        assert options.option_texts == ["1", "2"]
        assert options.row_texts == ["行一", "行二"]

    def test_collect_matrix_option_texts_handles_split_row_titles_without_rotating_headers(
        self,
    ) -> None:
        html = """
        <div id="div27">
          <table id="divRefTab27">
            <tr><td>1分</td><td>2分</td><td>3分</td><td>4分</td><td>5分</td></tr>
            <tr class="rowtitle" id="drv27_1t">
              <td class="title" colspan="3"><span class="itemTitleSpan">①学生个性化发展</span></td>
              <td class="title" colspan="2">理念培养</td>
            </tr>
            <tr id="drv27_1" rowindex="0" tp="d">
              <td><a class="rate-off" dval="1"></a></td>
              <td><a class="rate-off" dval="2"></a></td>
              <td><a class="rate-off" dval="3"></a></td>
              <td><a class="rate-off" dval="4"></a></td>
              <td><a class="rate-off" dval="5"></a></td>
            </tr>
            <tr class="rowtitle" id="drv27_2t">
              <td class="title" colspan="3"><span class="itemTitleSpan">②知识与学术能力</span></td>
              <td class="title" colspan="2">理念培养</td>
            </tr>
            <tr id="drv27_2" rowindex="1" tp="d">
              <td><a class="rate-off" dval="1"></a></td>
              <td><a class="rate-off" dval="2"></a></td>
              <td><a class="rate-off" dval="3"></a></td>
              <td><a class="rate-off" dval="4"></a></td>
              <td><a class="rate-off" dval="5"></a></td>
            </tr>
          </table>
        </div>
        """
        options = models_module._matrix_model(_features(html))

        assert options.matrix_rows == 2
        assert options.option_texts == ["1分", "2分", "3分", "4分", "5分"]
        assert options.row_texts == ["①学生个性化发展", "②知识与学术能力"]

    def test_slider_helpers_extract_range_and_matrix_metadata(self) -> None:
        html = """
        <div id="div8">
          <input id="q8" type="range" min="1" max="5" step="0.5" />
          <table>
            <tr class="rowtitletr"><td class="title">满意度</td></tr>
          </table>
          <div class="ruler"><span class="cm" data-value="1"></span><span class="cm" data-value="5"></span></div>
          <input class="ui-slider-input" rowid="1" min="1" max="5" step="2" />
          <div class="rangeslider"></div>
          <input class="ui-slider-input" rowid="2" min="1" max="5" step="2" />
          <div class="rangeslider"></div>
        </div>
        """
        features = _features(html)

        assert models_module.slider_range(features) == (1.0, 5.0, 0.5)
        assert Marker.SLIDER_MATRIX in features.markers
        assert models_module._format_slider_value(3.0) == "3"
        assert models_module._format_slider_value(3.5) == "3.5"
        assert models_module._slider_values(features.slider_inputs[0]) == ["1", "3", "5"]

        options = models_module._slider_matrix_model(features)
        assert options.matrix_rows == 2
        assert options.option_texts == ["1", "5"]
        assert options.row_texts == ["满意度"]
