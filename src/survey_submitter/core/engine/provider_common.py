from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from survey_submitter.core.persona.context import reset_context as _reset_answer_context
from survey_submitter.core.persona.generator import (
    generate_persona,
    reset_persona,
    set_current_persona,
)
from survey_submitter.core.questions.consistency import reset_consistency_context
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.core.questions.tendency import reset_tendency


@contextmanager
def provider_run_context(
    config: ExecutionConfig,
    *,
    state: ExecutionState | None = None,
    thread_name: str = "",
) -> Iterator[object | None]:

    if config.persona_enabled:
        persona = generate_persona()
        set_current_persona(persona)
    _reset_answer_context()
    reset_tendency()
    reset_consistency_context(config.answer_rules, list((config.questions_metadata or {}).values()))

    try:
        yield None
    finally:
        reset_persona()


__all__ = [
    "provider_run_context",
]
