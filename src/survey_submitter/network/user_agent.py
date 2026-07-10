from __future__ import annotations

from survey_submitter.core.task import ExecutionState
from survey_submitter.core.config.codec import UserAgentProfile, _select_user_agent_from_ratios


def _select_user_agent_for_session(ctx: ExecutionState) -> UserAgentProfile | None:
    if not ctx.config.random_user_agent_enabled:
        return None
    return _select_user_agent_from_ratios(ctx.config.user_agent_ratios)
