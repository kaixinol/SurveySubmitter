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

    if config.test_profiles and config.test_profiles_random:
        config.current_profile_index = random.randint(0, len(config.test_profiles) - 1)

    reset_answer_context()
    reset_tendency()
    reset_consistency_context(config.answer_rules, list((config.questions_metadata or {}).values()))

    yield None


__all__ = [
    "provider_run_context",
]
