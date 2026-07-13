from __future__ import annotations

import asyncio
from types import SimpleNamespace

import survey_submitter.core.engine.async_proxy_session as proxy_session_module
from survey_submitter.core.engine.async_proxy_session import AsyncProxySession
from survey_submitter.core.task import ExecutionConfig, ExecutionState


class AsyncProxySessionTests:
    def test_select_user_agent_caches_selected_profile(self, monkeypatch) -> None:
        profile = SimpleNamespace(category="mobile", ua="UA", label="安卓手机浏览器")
        monkeypatch.setattr(
            proxy_session_module,
            "_select_user_agent_for_session",
            lambda *_args, **_kwargs: profile,
            raising=False,
        )

        config = ExecutionConfig(random_user_agent=True)
        state = ExecutionState(config=config)
        session = AsyncProxySession(
            config=config,
            state=state,
            slot_label="Slot-1",
            stop_signal=state.stop_event,
            update_step=lambda _text: None,
        )

        ua = asyncio.run(session.select_user_agent())

        assert ua == "UA"
        assert session.user_agent_profile is profile
