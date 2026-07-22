from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from survey_submitter.core.config.schema import QuestionInfo
from survey_submitter.core.questions.normalization import configure_probabilities
from survey_submitter.core.questions.schema import (
    GLOBAL_RELIABILITY_DIMENSION,
    ChoiceQuestionAnswerConfig,
    LocationQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    QuestionDetail,
    TextQuestionAnswerConfig,
    _TEXT_RANDOM_ID_CARD,
    _TEXT_RANDOM_ID_CARD_TOKEN,
    _TEXT_RANDOM_INTEGER,
    _TEXT_RANDOM_MOBILE,
    _TEXT_RANDOM_MOBILE_TOKEN,
    _TEXT_RANDOM_NAME,
    _TEXT_RANDOM_NAME_TOKEN,
)
from survey_submitter.providers.contracts import ensure_survey_question_meta


class NormalizationRuntimeTests:
    def test_configure_probabilities_single_with_fill_texts_and_attached_selects(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=1,
                question_type="single",
                options=["", "", ""],
                details=QuestionDetail(
                    probabilities=[0, 3, 0],
                    answer_config=ChoiceQuestionAnswerConfig(
                        option_fill_texts=["", "补充", None],
                        attached_option_selects=[{"option_index": 1, "weights": [1, 0]}],
                    ),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.question_config_index_map[1] == ("single", 0)
        assert ctx.single_prob == [[0.0, 1.0, 0.0]]
        assert ctx.single_option_fill_texts == [[None, "补充", None]]
        assert ctx.single_attached_option_selects == [[{"option_index": 1, "weights": [1, 0]}]]

    def test_configure_probabilities_dropdown_with_dimension(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=2,
                question_type="dropdown",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[1, 1],
                    dimension="满意度",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.question_dimension_map[2] == "满意度"

    def test_configure_probabilities_multiple_with_fill_texts(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=3,
                question_type="multiple",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[25, 75],
                    answer_config=ChoiceQuestionAnswerConfig(
                        option_fill_texts=["A", "B"],
                    ),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.multiple_prob == [[25.0, 75.0]]
        assert ctx.multiple_option_fill_texts == [["A", "B"]]

    def test_configure_probabilities_matrix_with_dimension(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=4,
                question_type="matrix",
                options=["A", "B", "C"],
                details=QuestionDetail(
                    probabilities=[[1, 0, 0], [0, 2, 0]],
                    dimension="态度",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.matrix_prob == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        assert ctx.question_dimension_map[4] == "态度"

    def test_configure_probabilities_scale_normalizes_to_sum_one(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=5,
                question_type="scale",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[1, 3],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.scale_prob == [[0.25, 0.75]]

    def test_configure_probabilities_score_uses_equal_weights_when_unset(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=6,
                question_type="score",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[2, 2],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.scale_prob == [[0.5, 0.5]]

    def test_configure_probabilities_slider_custom_and_random_modes(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=7,
                question_type="slider",
                options=["A"],
                details=QuestionDetail(
                    probabilities=[75],
                    distribution_mode="custom",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
            QuestionInfo(
                num=8,
                question_type="slider",
                options=["A"],
                details=QuestionDetail(
                    probabilities=-1,
                    distribution_mode="random",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.slider_targets[0] == 75.0
        assert math.isnan(ctx.slider_targets[1])

    def test_configure_probabilities_order_uses_negative_one(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=9,
                question_type="order",
                options=[],
                details=QuestionDetail(
                    probabilities=-1,
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.question_config_index_map[9] == ("order", -1)

    def test_configure_probabilities_text_with_ai_enabled(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=10,
                question_type="text",
                options=["甲", "乙"],
                title="填空",
                details=QuestionDetail(
                    probabilities=[1, 3],
                    answer_config=TextQuestionAnswerConfig(
                        ai_enabled=True,
                    ),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.texts[0] == ["甲", "乙"]
        assert ctx.texts_prob[0] == [0.25, 0.75]
        assert ctx.text_ai_flags[0] is True
        assert ctx.text_titles[0] == "填空"

    def test_configure_probabilities_multi_text_blank_modes(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=11,
                question_type="multi_text",
                options=[],
                details=QuestionDetail(
                    probabilities=-1,
                    answer_config=MultiTextQuestionAnswerConfig(
                        ai_enabled=False,
                        multi_text_blank_modes=[_TEXT_RANDOM_INTEGER, "none"],
                        multi_text_blank_ai_flags=[True, True],
                        multi_text_blank_int_ranges=[[9, 3], []],
                    ),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.text_entry_types == ["multi_text"]
        assert ctx.text_ai_flags[0] is True
        assert ctx.multi_text_blank_int_ranges[0] == [[3, 9], []]

    def test_configure_probabilities_location_with_parts(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=12,
                question_type="text",
                options=["位置"],
                details=QuestionDetail(
                    probabilities=-1,
                    answer_config=LocationQuestionAnswerConfig(
                        location_parts=["北京", "北京", "东城区"],
                    ),
                ),
            ),
        ]
        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]
        assert ctx.question_config_index_map[12] == ("location", -1)
        assert ctx.location_parts[12] == ["北京", "北京", "东城区"]

    def test_configure_probabilities_builds_provider_question_mapping(self) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=2,
                question_type="scale",
                options=["A", "B", "C"],
                details=QuestionDetail(
                    probabilities=[1, 2, 3],
                    provider_page_id="4",
                    provider_question_id="question-1",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
            QuestionInfo(
                num=2,
                question_type="scale",
                options=["A", "B", "C"],
                details=QuestionDetail(
                    probabilities=[3, 2, 1],
                    provider_page_id="5",
                    provider_question_id="question-1",
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]

        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]

        assert ctx.question_config_index_map[2] == ("scale", 1)
        assert ctx.provider_question_idx_map == {
            "wjx:4:question-1": ("scale", 0),
            "wjx:5:question-1": ("scale", 1),
        }

    def test_configure_probabilities_assigns_global_reliability_dimension_when_none_explicit(
        self,
    ) -> None:
        ctx = SimpleNamespace()
        entries = [
            QuestionInfo(
                num=1,
                question_type="dropdown",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[1, 1],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
            QuestionInfo(
                num=2,
                question_type="scale",
                options=["A", "B"],
                details=QuestionDetail(
                    probabilities=[1, 1],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]

        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]

        assert ctx.question_dimension_map == {
            1: GLOBAL_RELIABILITY_DIMENSION,
            2: GLOBAL_RELIABILITY_DIMENSION,
        }

    def test_configure_probabilities_adds_only_ordinal_single_to_reliability(self) -> None:
        ctx = SimpleNamespace(
            questions_metadata={
                1: ensure_survey_question_meta(
                    {
                        "num": 1,
                        "title": "满意度",
                        "type_code": "3",
                        "option_texts": ["非常满意", "满意", "一般", "不满意", "非常不满意"],
                    }
                ),
                2: ensure_survey_question_meta(
                    {"num": 2, "title": "性别", "type_code": "3", "option_texts": ["男", "女"]}
                ),
            }
        )
        entries = [
            QuestionInfo(
                num=1,
                question_type="single",
                options=["非常满意", "满意", "一般", "不满意", "非常不满意"],
                details=QuestionDetail(
                    probabilities=[1, 1, 1, 1, 1],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
            QuestionInfo(
                num=2,
                question_type="single",
                options=["男", "女"],
                details=QuestionDetail(
                    probabilities=[1, 1],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]

        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]

        assert ctx.question_dimension_map == {1: GLOBAL_RELIABILITY_DIMENSION}

    def test_configure_probabilities_adds_obvious_attitude_single_to_reliability(self) -> None:
        ctx = SimpleNamespace(
            questions_metadata={
                4: ensure_survey_question_meta(
                    {
                        "num": 4,
                        "title": "年轻人应该先完成学业或事业起步，再考虑生育",
                        "type_code": "3",
                        "option_texts": [
                            "非常不同意",
                            "比较不同意",
                            "没意见",
                            "比较同意",
                            "非常同意",
                        ],
                    }
                ),
            }
        )
        entries = [
            QuestionInfo(
                num=4,
                question_type="single",
                options=[
                    "非常不同意",
                    "比较不同意",
                    "没意见",
                    "比较同意",
                    "非常同意",
                ],
                details=QuestionDetail(
                    probabilities=[1, 1, 1, 1, 1],
                    answer_config=ChoiceQuestionAnswerConfig(),
                ),
            ),
        ]

        configure_probabilities(entries, ctx)  # ty:ignore[invalid-argument-type]

        assert ctx.question_dimension_map == {4: GLOBAL_RELIABILITY_DIMENSION}

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            (
                QuestionInfo(
                    num=1,
                    question_type="single",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=[0, 0],
                        answer_config=ChoiceQuestionAnswerConfig(),
                    ),
                ),
                "所有选项配比均为 0",
            ),
            (
                QuestionInfo(
                    num=2,
                    question_type="dropdown",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=[0, 0],
                        answer_config=ChoiceQuestionAnswerConfig(),
                    ),
                ),
                "所有选项配比均为 0",
            ),
            (
                QuestionInfo(
                    num=3,
                    question_type="matrix",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=[[1, 0], [0, 0]],
                        answer_config=ChoiceQuestionAnswerConfig(),
                    ),
                ),
                "第 2 行配比全部为 0",
            ),
            (
                QuestionInfo(
                    num=4,
                    question_type="matrix",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=[0, 0],
                        answer_config=ChoiceQuestionAnswerConfig(),
                    ),
                ),
                "矩阵题",
            ),
            (
                QuestionInfo(
                    num=5,
                    question_type="multiple",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=ChoiceQuestionAnswerConfig(),
                    ),
                ),
                "多选题必须提供概率列表",
            ),
            (
                QuestionInfo(
                    num=6,
                    question_type="text",
                    options=[],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=TextQuestionAnswerConfig(),
                    ),
                ),
                "填空题至少需要一个候选答案",
            ),
            (
                QuestionInfo(
                    num=7,
                    question_type="text",
                    options=[],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=TextQuestionAnswerConfig(
                            text_random_mode=_TEXT_RANDOM_INTEGER,
                            text_random_int_range=[],
                        ),
                    ),
                ),
                "随机整数范围未设置完整",
            ),
            (
                QuestionInfo(
                    num=8,
                    question_type="multi_text",
                    options=["x"],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=MultiTextQuestionAnswerConfig(
                            multi_text_blank_modes=[_TEXT_RANDOM_INTEGER],
                            multi_text_blank_int_ranges=[[]],
                        ),
                    ),
                ),
                "多项填空题第1个空位",
            ),
            (
                QuestionInfo(
                    num=9,
                    question_type="single",
                    options=["A", "B"],
                    details=QuestionDetail(
                        probabilities=[1, 1],
                        answer_config=ChoiceQuestionAnswerConfig(
                            attached_option_selects=[{"option_text": "A", "weights": [0, 0]}],
                        ),
                    ),
                ),
                "嵌入式下拉",
            ),
        ],
    )
    def test_configure_probabilities_rejects_invalid_configs(
        self, entry: QuestionInfo, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            configure_probabilities([entry], SimpleNamespace())  # ty:ignore[invalid-argument-type]

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (_TEXT_RANDOM_NAME, _TEXT_RANDOM_NAME_TOKEN),
            (_TEXT_RANDOM_MOBILE, _TEXT_RANDOM_MOBILE_TOKEN),
            (_TEXT_RANDOM_ID_CARD, _TEXT_RANDOM_ID_CARD_TOKEN),
        ],
    )
    def test_text_random_modes_override_ai_and_candidate_text(
        self, mode: str, expected: str
    ) -> None:
        ctx = SimpleNamespace()
        configure_probabilities(
            [
                QuestionInfo(
                    num=1,
                    question_type="text",
                    options=["原值"],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=TextQuestionAnswerConfig(
                            ai_enabled=True,
                            text_random_mode=mode,
                        ),
                    ),
                )
            ],
            ctx,  # ty:ignore[invalid-argument-type]
        )

        assert ctx.texts == [[expected]]
        assert ctx.text_ai_flags == [False]

    def test_text_random_integer_builds_runtime_token(self) -> None:
        ctx = SimpleNamespace()

        configure_probabilities(
            [
                QuestionInfo(
                    num=1,
                    question_type="text",
                    options=["原值"],
                    details=QuestionDetail(
                        probabilities=-1,
                        answer_config=TextQuestionAnswerConfig(
                            text_random_mode=_TEXT_RANDOM_INTEGER,
                            text_random_int_range=[9, 3],
                        ),
                    ),
                )
            ],
            ctx,  # ty:ignore[invalid-argument-type]
        )

        assert ctx.texts == [["__RANDOM_INT__:3:9"]]
