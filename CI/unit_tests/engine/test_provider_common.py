from __future__ import annotations
from unittest.mock import patch
from survey_submitter.core.engine.provider_common import provider_run_context
from survey_submitter.core.task import ExecutionConfig
from survey_submitter.providers.contracts import SurveyQuestionMeta


class ProviderCommonTests:

    def test_provider_run_context_uses_explicit_plan_and_resets_persona(self) -> None:
        config = ExecutionConfig(answer_rules=[{'num': 1}], questions_metadata={1: SurveyQuestionMeta(num=1, title='Q1')})
        with patch('survey_submitter.core.engine.provider_common.generate_persona', return_value={'name': 'p'}), patch('survey_submitter.core.engine.provider_common.set_current_persona') as set_persona_mock, patch('survey_submitter.core.engine.provider_common._reset_answer_context') as reset_context_mock, patch('survey_submitter.core.engine.provider_common.reset_tendency') as reset_tendency_mock, patch('survey_submitter.core.engine.provider_common.reset_consistency_context') as reset_consistency_mock, patch('survey_submitter.core.engine.provider_common.reset_persona') as reset_persona_mock:
            with provider_run_context(config, psycho_plan='manual-plan') as resolved:
                assert resolved == 'manual-plan'
        set_persona_mock.assert_called_once_with({'name': 'p'})
        reset_context_mock.assert_called_once()
        reset_tendency_mock.assert_called_once()
        reset_consistency_mock.assert_called_once_with(config.answer_rules, [SurveyQuestionMeta(num=1, title='Q1')])
        reset_persona_mock.assert_called_once()
