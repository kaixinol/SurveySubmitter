from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock

import pytest

from survey_submitter.integrations.ai.client import save_ai_settings
import survey_submitter.integrations.ai.protocols as protocols
from survey_submitter.integrations.ai.protocols import (
    extract_chat_completion_text,
    extract_responses_text,
    is_endpoint_mismatch_error,
    resolve_custom_endpoint,
)


class AIProtocolTests:
    def setup_method(self, _method) -> None:
        save_ai_settings(
            api_key="test-key",
            base_url="https://example.com/v1",
            api_protocol="responses",
            model="demo-model",
            system_prompt="测试提示词",
        )

    def testresolve_custom_endpoint_appends_protocol_suffix(self) -> None:
        protocol, url, explicit = resolve_custom_endpoint("https://example.com/v1", "responses")
        assert protocol == "responses"
        assert url == "https://example.com/v1/responses"
        assert not explicit

    def testresolve_custom_endpoint_handles_explicit_and_invalid_urls(self) -> None:
        assert resolve_custom_endpoint(" https://example.com/v1/chat/completions/ ", "auto") == (
            "chat_completions",
            "https://example.com/v1/chat/completions",
            True,
        )
        assert resolve_custom_endpoint("https://example.com/v1/responses", "chat") == (
            "responses",
            "https://example.com/v1/responses",
            True,
        )
        with pytest.raises(RuntimeError, match="旧版 /completions"):
            resolve_custom_endpoint("https://example.com/v1/completions", "auto")
        with pytest.raises(RuntimeError, match="Base URL"):
            resolve_custom_endpoint("   ", "auto")

    def testextract_chat_completion_text_prefers_message_content(self) -> None:
        text = extract_chat_completion_text(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一句"},
                                {"type": "output_text", "text": "第二句"},
                            ]
                        }
                    }
                ]
            }
        )
        assert text == "第一句\n第二句"

    def testextract_responses_text_reads_output_content(self) -> None:
        text = extract_responses_text(
            {"output": [{"content": [{"type": "output_text", "text": "连接成功"}]}]}
        )
        assert text == "连接成功"

    def test_extract_text_helpers_cover_strings_empty_and_top_level_responses(self) -> None:
        assert (
            extract_chat_completion_text({"choices": [{"message": {"content": "  直接文本  "}}]})
            == "直接文本"
        )
        assert extract_responses_text({"output_text": "  顶层文本  "}) == "顶层文本"
        with pytest.raises(RuntimeError, match="choices"):
            extract_chat_completion_text({})
        with pytest.raises(RuntimeError, match="内容为空"):
            extract_chat_completion_text({"choices": [{"message": {"content": []}}]})
        with pytest.raises(RuntimeError, match="内容为空"):
            extract_responses_text({"output": [{"content": [{"type": "image", "text": "忽略"}]}]})

    def testis_endpoint_mismatch_error(self) -> None:
        assert is_endpoint_mismatch_error(RuntimeError("405 method not allowed"))
        assert not is_endpoint_mismatch_error(RuntimeError("quota exceeded"))

        from types import SimpleNamespace

        from openai import APIStatusError

        mock_response = SimpleNamespace(
            status_code=404, request=SimpleNamespace(), headers={}
        )
        status_err = APIStatusError("not found", response=mock_response, body=None)  # ty:ignore[invalid-argument-type]
        assert is_endpoint_mismatch_error(status_err)

    @pytest.mark.asyncio
    async def test_acall_chat_completions_uses_openai_client(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        mock_create = AsyncMock()
        mock_create.return_value.choices = [
            type("M", (), {"message": type("M", (), {"content": "chat ok"})()})()
        ]

        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_create

        mock_client_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr(protocols, "AsyncOpenAI", mock_client_cls)

        result = await protocols.acall_chat_completions(
            "https://api.example.com/v1/chat/completions", "k", "m", "问题", "系统"
        )
        assert result == "chat ok"
        mock_client_cls.assert_called_once_with(
            base_url="https://api.example.com/v1",
            api_key="k",
            timeout=30.0,
            max_retries=2,
        )
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["model"] == "m"
        assert call_kwargs["messages"][1]["content"] == "请简短回答这个问卷问题：问题"

    @pytest.mark.asyncio
    async def test_acall_responses_uses_openai_client(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        mock_create = AsyncMock()
        mock_create.return_value.output_text = "responses ok"

        mock_instance = MagicMock()
        mock_instance.responses.create = mock_create

        mock_client_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr(protocols, "AsyncOpenAI", mock_client_cls)

        result = await protocols.acall_responses(
            "https://api.example.com/v1/responses", "k", "m", "问题", "系统"
        )
        assert result == "responses ok"
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["input"] == "请简短回答这个问卷问题：问题"
        assert call_kwargs["instructions"] == "系统"

    @pytest.mark.asyncio
    async def test_acall_wraps_openai_errors(self, monkeypatch) -> None:
        from unittest.mock import MagicMock

        from types import SimpleNamespace

        from openai import APIError

        mock_create = AsyncMock(side_effect=APIError("boom", request=SimpleNamespace(), body=None))  # ty:ignore[invalid-argument-type]

        mock_instance = MagicMock()
        mock_instance.chat.completions.create = mock_create

        mock_client_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr(protocols, "AsyncOpenAI", mock_client_cls)

        with pytest.raises(RuntimeError, match="API 调用失败"):
            await protocols.acall_chat_completions(
                "https://api.example.com/v1", "k", "m", "问题", "系统"
            )

    def test_generate_answer_tries_chat_then_falls_back_to_responses_in_auto_mode(self) -> None:
        import survey_submitter.integrations.ai.client as client_module

        original_chat = client_module.acall_chat_completions
        original_responses = client_module.acall_responses
        save_ai_settings(api_protocol="auto")
        calls: list[str] = []

        async def _fake_chat(*_args, **_kwargs):
            calls.append("chat")
            raise RuntimeError("404 not found")

        async def _fake_responses(*_args, **_kwargs):
            calls.append("responses")
            return "回退成功"

        client_module.acall_chat_completions = _fake_chat  # ty:ignore[invalid-assignment]
        client_module.acall_responses = _fake_responses  # ty:ignore[invalid-assignment]
        try:
            answer = asyncio.run(
                client_module.agenerate_answer(
                    "测试问题",
                    question_type="fill_blank",
                    blank_count=1,
                )
            )
        finally:
            client_module.acall_chat_completions = original_chat
            client_module.acall_responses = original_responses
        assert answer == "回退成功"
        assert calls == ["chat", "responses"]
