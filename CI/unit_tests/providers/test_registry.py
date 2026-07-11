from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from survey_submitter.core.task import ExecutionConfig, ExecutionState
from survey_submitter.providers import registry
from survey_submitter.providers.common import SURVEY_PROVIDER_WJX
from survey_submitter.providers.contracts import SurveyDefinition
from survey_submitter.providers.hooks import build_fill_http_hook, build_parse_hook


async def test_parse_survey_routes_detected_provider_directly() -> None:
    wjx_url = "https://www.wjx.cn/vm/demo.aspx"
    adapter = SimpleNamespace(parse_survey_async=AsyncMock(return_value="wjx-definition"))

    with patch.object(registry, "_get_provider_adapter", return_value=adapter):
        result = await registry.parse_survey(wjx_url)

    assert result == "wjx-definition"
    adapter.parse_survey_async.assert_awaited_once_with(wjx_url)


async def test_fill_survey_http_routes_wjx_adapter() -> None:
    state = ExecutionState(config=ExecutionConfig(survey_provider=SURVEY_PROVIDER_WJX))
    adapter = registry._PROVIDER_REGISTRY[SURVEY_PROVIDER_WJX]

    @contextmanager
    def fake_provider_run_context(*_args, **_kwargs):
        yield "resolved-plan"

    with (
        patch.object(
            adapter, "fill_survey_http_async", new=AsyncMock(return_value=True)
        ) as fill_mock,
        patch.object(registry, "provider_run_context", fake_provider_run_context),
    ):
        result = await registry.fill_survey_http(
            ExecutionConfig(survey_provider=SURVEY_PROVIDER_WJX),
            state,
            stop_signal="stop",
            thread_name="Worker-1",
            psycho_plan="ignored-plan",
            provider=SURVEY_PROVIDER_WJX,
            proxy_address="http://1.1.1.1:80",
            user_agent="UA",
        )

    assert result is True
    assert fill_mock.await_args is not None
    assert fill_mock.await_args.kwargs["psycho_plan"] == "resolved-plan"
    assert fill_mock.await_args.kwargs["proxy_address"] == "http://1.1.1.1:80"
    assert fill_mock.await_args.kwargs["user_agent"] == "UA"


async def test_provider_hook_rejects_non_async_return_value() -> None:
    with patch(
        "survey_submitter.providers.hooks._load_hook", return_value=lambda *_args, **_kwargs: True
    ):
        hook = build_fill_http_hook(("fake.module", "sync_http_fill"))
        try:
            await hook(ExecutionConfig(), ExecutionState())
        except TypeError as exc:
            assert "provider hook 必须返回 awaitable" in str(exc)
        else:
            raise AssertionError("同步 provider hook 不应再被接受")


async def test_parse_hook_normalizes_tuple_result_to_survey_definition() -> None:
    async def fake_parse(_url: str):
        return ([{"num": 1, "title": "Q1", "type_code": "3"}], "  标题  ")

    with patch("survey_submitter.providers.hooks._load_hook", return_value=fake_parse):
        hook = build_parse_hook(SURVEY_PROVIDER_WJX, ("fake.module", "parse"))
        definition = await hook("https://www.wjx.cn/vm/demo.aspx")

    assert isinstance(definition, SurveyDefinition)
    assert definition.provider == SURVEY_PROVIDER_WJX
    assert definition.title == "标题"


async def test_parse_hook_rejects_sync_parser_result() -> None:
    with patch(
        "survey_submitter.providers.hooks._load_hook", return_value=lambda _url: ([], "标题")
    ):
        hook = build_parse_hook(SURVEY_PROVIDER_WJX, ("fake.module", "parse"))
        try:
            await hook("https://www.wjx.cn/vm/demo.aspx")
        except TypeError as exc:
            assert "解析 hook 必须返回 awaitable" in str(exc)
        else:
            raise AssertionError("同步解析 hook 不应再被接受")
