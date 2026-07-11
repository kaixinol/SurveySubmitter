from __future__ import annotations

from survey_submitter.constants import DEFAULT_FILL_TEXT
from survey_submitter.core.questions.default_builder import build_default_question_entries
from survey_submitter.core.questions.schema import (
    QuestionEntry,
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

        entries = build_default_question_entries(
            questions, survey_url="https://www.wjx.cn/vm/demo.aspx"
        )

        assert [entry.question_num for entry in entries] == [1, 2, 3, 4, 5, 6]
        assert entries[0].question_type == "single"
        assert entries[0].probabilities == [0.0, 0.0, 1.0, 0.0]
        assert entries[0].distribution_mode == "custom"
        assert entries[1].question_type == "multiple"
        assert entries[1].probabilities == [50.0, 50.0, 50.0]
        assert entries[2].question_type == "matrix"
        assert entries[2].rows == 2
        assert entries[3].question_type == "score"
        assert entries[3].option_count == 7
        assert entries[4].question_type == "slider"
        assert entries[4].probabilities == [15.0]
        assert entries[5].question_type == "text"
        assert entries[5].texts == [DEFAULT_FILL_TEXT]

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

        entries = build_default_question_entries(
            questions, survey_url="https://www.wjx.cn/vm/demo.aspx"
        )

        assert entries[0].question_type == "multi_text"
        assert entries[0].multi_text_blank_modes == [
            _TEXT_RANDOM_NONE,
            _TEXT_RANDOM_MOBILE,
            _TEXT_RANDOM_NONE,
        ]

    def test_build_default_question_entries_reuses_existing_by_provider_num_and_title(self) -> None:
        existing_by_provider = QuestionEntry(
            question_type="single",
            probabilities=[0, 1],
            option_count=2,
            question_num=99,
            question_title="旧标题",
            survey_provider="wjx",
            provider_question_id="provider-1",
            distribution_mode="custom",
            custom_weights=[0, 1],
            option_fill_texts=["", "其他"],
            fillable_option_indices=[1],
            attached_option_selects=[{"option_index": 1, "weights": [1, 0]}],
        )
        existing_by_num = QuestionEntry(
            question_type="multiple",
            probabilities=[10, 90],
            option_count=2,
            question_num=2,
            question_title="多选题",
            distribution_mode="custom",
            custom_weights=[10, 90],
        )
        existing_by_title = QuestionEntry(
            question_type="text",
            probabilities=[1],
            texts=["旧答案"],
            question_num=88,
            question_title="标题匹配",
            ai_enabled=True,
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

        entries = build_default_question_entries(
            questions,
            existing_entries=[existing_by_provider, existing_by_num, existing_by_title],
        )

        assert entries[0].probabilities == [0, 1]
        assert entries[0].custom_weights == [0, 1]
        assert entries[0].option_fill_texts == [None, "其他"]
        assert entries[0].fillable_option_indices == [1]
        assert entries[0].attached_option_selects == [
            {
                "option_index": 1,
                "option_text": "其他",
                "select_options": ["北京", "上海"],
                "weights": [1.0, 0.0],
            }
        ]
        assert entries[1].probabilities == [10, 90]
        assert entries[1].distribution_mode == "custom"
        assert entries[2].texts == ["旧答案"]
        assert entries[2].ai_enabled is True

    def test_build_default_question_entries_drops_stale_option_fill_texts(self) -> None:
        existing = QuestionEntry(
            question_type="single",
            probabilities=[1, 0],
            option_count=2,
            question_num=1,
            question_title="单选题",
            option_fill_texts=["旧填空", None],
            fillable_option_indices=[0],
        )
        entries = build_default_question_entries(
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

        assert entries[0].fillable_option_indices == []
        assert entries[0].option_fill_texts is None

    def test_build_default_question_entries_does_not_reuse_mismatched_title_or_type(self) -> None:
        existing = QuestionEntry(
            question_type="single",
            probabilities=[0, 1],
            option_count=2,
            question_num=1,
            question_title="旧标题",
            distribution_mode="custom",
            custom_weights=[0, 1],
        )
        questions = [
            ensure_survey_question_meta(
                {"num": 1, "title": "新标题", "type_code": "3", "option_texts": ["A", "B"]}
            )
        ]

        entries = build_default_question_entries(questions, existing_entries=[existing])

        assert entries[0].probabilities == -1
        assert entries[0].distribution_mode == "random"
