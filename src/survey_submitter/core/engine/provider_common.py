from __future__ import annotations

import random
from contextlib import contextmanager
from typing import Iterator

from survey_submitter.core.engine.answer_context import reset_answer_context
from survey_submitter.core.questions.consistency import reset_consistency_context
from survey_submitter.core.questions.tendency import reset_tendency
from survey_submitter.core.task import ExecutionConfig, ExecutionState


@contextmanager
def provider_run_context(
    config: ExecutionConfig,
    *,
    state: ExecutionState | None = None,
    thread_name: str = "",
) -> Iterator[object | None]:

    if state is not None and config.test_profiles.profiles and config.test_profiles.random:
        state.current_profile_index = random.randint(0, len(config.test_profiles.profiles) - 1)

    reset_answer_context()
    reset_tendency()
    reset_consistency_context(config.answer_policy.rules, list((config.question_maps.questions_metadata or {}).values()))

    yield None


__all__ = [
    "provider_run_context",
]
