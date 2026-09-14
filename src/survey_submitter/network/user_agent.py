from __future__ import annotations

from survey_submitter.core.config.codec import UserAgentProfile, _select_user_agent_from_ratios
from survey_submitter.core.task import ExecutionState


def _select_user_agent_for_session(ctx: ExecutionState) -> UserAgentProfile | None:
    if not ctx.config.network.random_user_agent:
        return None
    return _select_user_agent_from_ratios(ctx.config.network.user_agent_ratios)
