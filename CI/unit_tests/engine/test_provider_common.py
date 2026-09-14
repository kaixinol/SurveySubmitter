from __future__ import annotations

from unittest.mock import patch

from survey_submitter.core.engine.provider_common import provider_run_context
from survey_submitter.core.task import ExecutionConfig
from survey_submitter.providers.contracts import SurveyQuestionMeta


class ProviderCommonTests:
    def test_provider_run_context_resets_runtime_state(self) -> None:
        config = ExecutionConfig(answer_policy={'rules': [{"num": 1}]}, question_maps={'questions_metadata': {1: SurveyQuestionMeta(num=1, title="Q1")}})
        with (
            patch(
                "survey_submitter.core.engine.provider_common.reset_answer_context"
            ) as reset_answer_context_mock,
            patch(
                "survey_submitter.core.engine.provider_common.reset_tendency"
            ) as reset_tendency_mock,
            patch(
                "survey_submitter.core.engine.provider_common.reset_consistency_context"
            ) as reset_consistency_mock,
        ):
            with provider_run_context(config):
                pass
        reset_answer_context_mock.assert_called_once()
        reset_tendency_mock.assert_called_once()
        reset_consistency_mock.assert_called_once_with(
            config.answer_policy.rules, [SurveyQuestionMeta(num=1, title="Q1")]
        )
