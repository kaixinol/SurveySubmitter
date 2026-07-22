from __future__ import annotations

from survey_submitter.constants import DEFAULT_FILL_TEXT
from survey_submitter.core.config.schema import QuestionInfo
from survey_submitter.core.questions.default_builder import build_default_survey_questions
from survey_submitter.core.questions.schema import (
    ChoiceQuestionAnswerConfig,
    MultiTextQuestionAnswerConfig,
    QuestionDetail,
    _TEXT_RANDOM_MOBILE,
    _TEXT_RANDOM_NONE,
)
from survey_submitter.providers.contracts import ensure_survey_question_meta


class DefaultBuilderRuntimeTests:
    def test_build_default_question_entries_creates_defaults_for_common_types(self) -> None:
        questions = [
            ensure_survey_question_meta(
                {
                    "num": 1,
                    "title": "单选",
                    "type_code": "3",
                    "option_texts": ["A", "B", "C", "D"],
                    "forced_option_index": 2,
                    "forced_option_text": "C",
                }
            ),
            ensure_survey_question_meta(
                {"num": 2, "title": "多选", "type_code": "4", "option_texts": ["A", "B", "C"]}
            ),
            ensure_survey_question_meta({"num": 3, "title": "矩阵", "type_code": "6", "rows": 2}),
            ensure_survey_question_meta(
                {"num": 4, "title": "评分", "type_code": "5", "rating_max": 7}
            ),
            ensure_survey_question_meta(
                {"num": 5, "title": "滑块", "type_code": "8", "slider_min": 10, "slider_max": 20}
            ),
            ensure_survey_question_meta(
                {
                    "num": 6,
                    "title": "填空",
                    "type_code": "1",
                    "text_inputs": 1,
                    "text_input_labels": ["指定文本"],
                }
            ),
            ensure_survey_question_meta({"num": 7, "title": "说明", "type_code": "description"}),
            ensure_survey_question_meta(
                {"num": 8, "title": "不支持", "type_code": "99", "unsupported": True}
            ),
        ]

        entries = build_default_survey_questions(
            questions, survey_url="https://www.wjx.cn/vm/demo.aspx"
        )

        assert [qi.num for qi in entries] == [1, 2, 3, 4, 5, 6]
        assert entries[0].question_type == "single"
        assert entries[0].details.probabilities == [0.0, 0.0, 1.0, 0.0]
        assert entries[0].details.distribution_mode == "custom"
        assert entries[1].question_type == "multiple"
        assert entries[1].details.probabilities == [50.0, 50.0, 50.0]
        assert entries[2].question_type == "matrix"
        assert entries[3].question_type == "score"
        assert entries[4].question_type == "slider"
        assert entries[4].details.probabilities == [15.0]
        assert entries[5].question_type == "text"

    def test_build_default_question_entries_infers_multi_text_mobile_blank(self) -> None:
        questions = [
            ensure_survey_question_meta(
                {
                    "num": 11,
                    "title": "多项填空",
                    "type_code": "multi_text",
                    "text_inputs": 3,
                    "text_input_labels": ["项目评价", "请输入手机号", "备注"],
                }
            )
        ]

        entries = build_default_survey_questions(
            questions, survey_url="https://www.wjx.cn/vm/demo.aspx"
        )

        assert entries[0].question_type == "multi_text"
        assert isinstance(entries[0].details.answer_config, MultiTextQuestionAnswerConfig)
        assert entries[0].details.answer_config.multi_text_blank_modes == [
            _TEXT_RANDOM_NONE,
            _TEXT_RANDOM_MOBILE,
            _TEXT_RANDOM_NONE,
        ]

    def test_build_default_question_entries_reuses_existing_by_provider_num_and_title(self) -> None:
        existing_by_provider = QuestionInfo(
            num=99,
            title="旧标题",
            question_type="single",
            options=[],
            details=QuestionDetail(
                probabilities=[0, 1],
                distribution_mode="custom",
                custom_weights=[0, 1],
                provider_question_id="provider-1",
                answer_config=ChoiceQuestionAnswerConfig(
                    option_fill_texts=["", "其他"],
                    fillable_option_indices=[1],
                    attached_option_selects=[{"option_index": 1, "weights": [1, 0]}],
                ),
            ),
        )
        existing_by_num = QuestionInfo(
            num=2,
            title="多选题",
            question_type="multiple",
            options=[],
            details=QuestionDetail(
                probabilities=[10, 90],
                distribution_mode="custom",
                custom_weights=[10, 90],
                answer_config=ChoiceQuestionAnswerConfig(),
            ),
        )
        existing_by_title = QuestionInfo(
            num=88,
            title="标题匹配",
            question_type="text",
            options=["旧答案"],
            details=QuestionDetail(
                probabilities=[1],
                answer_config=ChoiceQuestionAnswerConfig(ai_enabled=True),
            ),
        )
        questions = [
            ensure_survey_question_meta(
                {
                    "num": 1,
                    "title": "新标题",
                    "type_code": "3",
                    "option_texts": ["X", "Y"],
                    "provider_question_id": "provider-1",
                    "fillable_options": [1],
                    "attached_option_selects": [
                        {
                            "option_index": "1",
                            "option_text": "其他",
                            "select_options": ["北京", "上海"],
                        }
                    ],
                }
            ),
            ensure_survey_question_meta(
                {"num": 2, "title": "多选题", "type_code": "4", "option_texts": ["A", "B"]}
            ),
            ensure_survey_question_meta(
                {"num": 3, "title": "标题匹配", "type_code": "1", "text_inputs": 1}
            ),
        ]

        entries = build_default_survey_questions(
            questions,
            existing_entries=[existing_by_provider, existing_by_num, existing_by_title],
        )

        assert entries[0].details.probabilities == [0, 1]
        assert entries[0].details.custom_weights == [0, 1]
        assert isinstance(entries[0].details.answer_config, ChoiceQuestionAnswerConfig)
        assert entries[0].details.answer_config.option_fill_texts == [None, "其他"]
        assert entries[0].details.answer_config.fillable_option_indices == [1]
        assert entries[0].details.answer_config.attached_option_selects == [
            {
                "option_index": 1,
                "option_text": "其他",
                "select_options": ["北京", "上海"],
                "weights": [1.0, 0.0],
            }
        ]
        assert entries[1].details.probabilities == [10, 90]
        assert entries[1].details.distribution_mode == "custom"
        assert entries[2].details.answer_config.ai_enabled is True

    def test_build_default_question_entries_drops_stale_option_fill_texts(self) -> None:
        existing = QuestionInfo(
            num=1,
            title="单选题",
            question_type="single",
            options=[],
            details=QuestionDetail(
                probabilities=[1, 0],
                answer_config=ChoiceQuestionAnswerConfig(
                    option_fill_texts=["旧填空", None],
                    fillable_option_indices=[0],
                ),
            ),
        )
        entries = build_default_survey_questions(
            [
                ensure_survey_question_meta(
                    {
                        "num": 1,
                        "title": "单选题",
                        "type_code": "3",
                        "option_texts": ["A", "B"],
                        "fillable_options": [5],
                    }
                )
            ],
            existing_entries=[existing],
        )

        assert isinstance(entries[0].details.answer_config, ChoiceQuestionAnswerConfig)
        assert entries[0].details.answer_config.fillable_option_indices == []
        assert entries[0].details.answer_config.option_fill_texts is None

    def test_build_default_question_entries_does_not_reuse_mismatched_title_or_type(self) -> None:
        existing = QuestionInfo(
            num=1,
            title="旧标题",
            question_type="single",
            options=[],
            details=QuestionDetail(
                probabilities=[0, 1],
                distribution_mode="custom",
                custom_weights=[0, 1],
                answer_config=ChoiceQuestionAnswerConfig(),
            ),
        )
        questions = [
            ensure_survey_question_meta(
                {"num": 1, "title": "新标题", "type_code": "3", "option_texts": ["A", "B"]}
            )
        ]

        entries = build_default_survey_questions(questions, existing_entries=[existing])

        assert entries[0].details.probabilities == -1
        assert entries[0].details.distribution_mode == "random"

    def test_build_default_question_entries_retains_attached_selects_for_multiple(self) -> None:
        questions = [
            ensure_survey_question_meta(
                {
                    "num": 2,
                    "title": "多选题",
                    "type_code": "4",
                    "option_texts": ["A", "B", "C"],
                    "fillable_options": [2],
                    "attached_option_selects": [
                        {
                            "option_index": "2",
                            "option_text": "其他",
                            "select_options": ["北京", "上海", "广州"],
                        }
                    ],
                }
            )
        ]

        entries = build_default_survey_questions(questions)

        assert entries[0].question_type == "multiple"
        assert isinstance(entries[0].details.answer_config, ChoiceQuestionAnswerConfig)
        assert entries[0].details.answer_config.attached_option_selects == [
            {
                "option_index": 2,
                "option_text": "其他",
                "select_options": ["北京", "上海", "广州"],
                "weights": None,
            }
        ]
