from __future__ import annotations

import pytest

from survey_submitter.core.questions.schema import (
    _infer_option_count,
    answer_config_type_for_question_type,
    ChoiceQuestionAnswerConfig,
    LocationQuestionAnswerConfig,
    UniversityQuestionAnswerConfig,
)


@pytest.mark.config
class SchemaHelperTests:
    def test_infer_option_count_prefers_matrix_nested_lengths(self) -> None:
        assert (
            _infer_option_count(
                "matrix",
                probabilities=[[0.1, 0.2, 0.7]],
                custom_weights=[[1, 2, 3, 4]],
            )
            == 4
        )

    def test_infer_option_count_uses_explicit_option_count_then_weights_probabilities_and_texts(
        self,
    ) -> None:
        assert _infer_option_count("single", probabilities=None, option_count=6) == 6
        assert _infer_option_count("single", probabilities=None, custom_weights=[1, 2, 3]) == 3
        assert _infer_option_count("single", probabilities=[0.2, 0.8]) == 2
        assert _infer_option_count("text", probabilities=None, texts=["a", "b", "c"]) == 3

    def test_infer_option_count_uses_scale_default_and_falls_back_to_zero(self) -> None:
        assert _infer_option_count("scale", probabilities=None) == 5
        assert _infer_option_count("single", probabilities=None) == 0

    def test_university_question_answer_config_defaults(self) -> None:
        config = UniversityQuestionAnswerConfig()
        assert config.random_value_pool is None
        assert config.ai_enabled is False

    def test_choice_question_answer_config_random_value_pool_defaults(self) -> None:
        config = ChoiceQuestionAnswerConfig()
        assert config.random_value_pool is None
        assert config.option_random_pools is None

    def test_location_question_answer_config_random_value_pool_defaults(self) -> None:
        config = LocationQuestionAnswerConfig()
        assert config.random_value_pool is None
        assert config.location_parts == []

    def test_answer_config_type_for_question_type_university(self) -> None:
        result = answer_config_type_for_question_type(
            "text", location_parts=["北京"], is_university=True
        )
        assert result is UniversityQuestionAnswerConfig

    def test_answer_config_type_for_question_type_location(self) -> None:
        result = answer_config_type_for_question_type(
            "text", location_parts=["北京"], is_university=False
        )
        assert result is LocationQuestionAnswerConfig

    def test_answer_config_type_for_question_type_choice(self) -> None:
        result = answer_config_type_for_question_type("single")
        assert result is ChoiceQuestionAnswerConfig
