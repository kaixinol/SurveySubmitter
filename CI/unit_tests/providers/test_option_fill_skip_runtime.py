from __future__ import annotations

import pytest

from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers.contracts import (
    SurveyQuestionMeta,
    ensure_survey_question_meta,
)
from survey_submitter.providers.wjx.answering_builders import _build_multiple_answer_action


@pytest.mark.config
class OptionalFillSkipRuntimeTests:
    def _meta(self) -> SurveyQuestionMeta:
        return ensure_survey_question_meta(
            {
                "num": 1,
                "title": "多选",
                "type_code": "4",
                "option_texts": ["A", "B", "C"],
                "fillable_options": [0, 1, 2],
                "required_fillable_options": [2],
            }
        )

    async def test_full_skip_ratio_drops_optional_fills_but_keeps_required(self) -> None:
        config = ExecutionConfig(choice_fill={'optional_fill_skip_ratio': 1.0, 'multiple_option_fill_texts': [["无", "无", "无"]]})
        ctx = ExecutionState(config=config)

        action = await _build_multiple_answer_action(
            [0, 1, 2],
            option_texts=["A", "B", "C"],
            option_count=3,
            config=config,
            config_index=0,
            question=self._meta(),
            current=1,
            ctx=ctx,
            allow_ai_placeholder=True,
        )

        assert action is not None
        assert action.option_fill_texts == ((2, "无"),)
        assert action.selected_texts == ("A", "B", "C / 无")

    async def test_zero_ratio_keeps_all_optional_fills(self) -> None:
        config = ExecutionConfig(choice_fill={'optional_fill_skip_ratio': 0.0, 'multiple_option_fill_texts': [["无", "无", "无"]]})
        ctx = ExecutionState(config=config)

        action = await _build_multiple_answer_action(
            [0, 1, 2],
            option_texts=["A", "B", "C"],
            option_count=3,
            config=config,
            config_index=0,
            question=self._meta(),
            current=1,
            ctx=ctx,
            allow_ai_placeholder=True,
        )

        assert action is not None
        assert action.option_fill_texts == ((0, "无"), (1, "无"), (2, "无"))
        assert action.selected_texts == ("A / 无", "B / 无", "C / 无")
