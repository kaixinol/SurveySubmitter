from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from survey_submitter.core.persona.context import reset_context as _reset_answer_context
from survey_submitter.core.persona.generator import generate_persona, reset_persona, set_current_persona
from survey_submitter.core.questions.config import GLOBAL_RELIABILITY_DIMENSION
from survey_submitter.core.questions.consistency import reset_consistency_context
from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.core.questions.tendency import reset_tendency


@contextmanager
def provider_run_context(
    config: ExecutionConfig,
    *,
    state: ExecutionState | None = None,
    thread_name: str = "",
    psycho_plan: Any | None = None,
) -> Iterator[Any | None]:
    
    persona = generate_persona()
    set_current_persona(persona)
    _reset_answer_context()
    reset_tendency()
    reset_consistency_context(config.answer_rules, list((config.questions_metadata or {}).values()))

    resolved_plan = psycho_plan

    if resolved_plan is not None:
        dimension_count = len(getattr(resolved_plan, "plans", {}) or {})
        plan_names = list((getattr(resolved_plan, "plans", {}) or {}).keys())
        if plan_names == [GLOBAL_RELIABILITY_DIMENSION]:
            dimension_summary = "全局未分组问卷"
        else:
            dimension_summary = ",".join(plan_names[:5]) if plan_names else "无"
        logging.info(
            "本轮启用心理测量计划：维度数=%d，题目数=%d，目标α=%.2f，维度=%s",
            dimension_count,
            len(getattr(resolved_plan, "items", []) or []),
            float(config.psycho_target_alpha or 0.85),
            dimension_summary,
        )

    try:
        yield resolved_plan
    finally:
        reset_persona()

__all__ = [
    "provider_run_context",
]
