from __future__ import annotations

from typing import Any

from pydantic import field_validator

from survey_submitter.core.config.base import BaseConfigModel

CUSTOM_API_PROTOCOLS = {
    "auto": {
        "label": "自动识别（推荐）",
        "description": "自动识别完整端点；只填 /v1 时自动尝试兼容协议",
    },
    "chat_completions": {
        "label": "Chat Completions",
        "description": "兼容 /chat/completions 协议",
    },
    "responses": {
        "label": "Responses",
        "description": "兼容 /responses 协议",
    },
}

_SYSTEM_PROMPT_BASE = (
    "你现在不是AI助手，而是一名有实际使用经验但不专业的普通用户。\n"
    "请按照\u201c填写问卷/填空题\u201d的方式作答，而不是进行解释或对话。\n\n"
    "回答规则：\n"
    "1. 只给出答案本身，不要解释原因，不要分析，不要教学\n"
    "2. 以个人体验和模糊印象为主，可以不确定、可以用模糊一些的表达\n"
    "3. 回答尽量简短，避免长句\n"
    "4. 不要使用专业术语或严谨表述\n\n"
    "请注意：\n"
    "- 不要像AI助手一样分点说明\n"
    "- 不要补充背景知识\n"
    "- 不要解释题目\n"
    "- 不要自称\u201c作为AI\u201d\n\n"
    "如果你的回答开始变得专业、详细或像在解释，请立即改回普通用户的随意回答风格。"
)

DEFAULT_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT_BASE + "\n\n多项填空补充规则：\n"
    "6. 当题目有多个空位时，按空位顺序输出一个字符串，并使用 || 分隔每个答案（示例：答案1||答案2||答案3）"
)

__all__ = [
    "CUSTOM_API_PROTOCOLS",
    "DEFAULT_SYSTEM_PROMPT",
    "AISettings",
    "get_ai_readiness_error",
    "get_ai_settings",
    "get_default_system_prompt",
]


def _normalize_custom_api_protocol(value: Any) -> str:
    protocol = value or "auto".lower()
    if protocol in CUSTOM_API_PROTOCOLS:
        return protocol
    return "auto"


class AISettings(BaseConfigModel):
    api_key: str = ""
    base_url: str = ""
    api_protocol: str = "auto"
    model: str = ""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @field_validator("api_protocol")
    @classmethod
    def normalize_api_protocol(cls, v: str) -> str:
        protocol = v or "auto".lower()
        if protocol in CUSTOM_API_PROTOCOLS:
            return protocol
        return "auto"

    @field_validator("base_url", "model")
    @classmethod
    def strip_string(cls, v: str) -> str:
        return v or ""

    @field_validator("system_prompt")
    @classmethod
    def normalize_system_prompt(cls, v: str) -> str:
        prompt = v or ""
        return prompt or DEFAULT_SYSTEM_PROMPT


def get_default_system_prompt() -> str:
    return DEFAULT_SYSTEM_PROMPT


def get_ai_settings() -> dict[str, Any]:
    return AISettings().model_dump()


def get_ai_readiness_error(config: dict[str, Any] | None = None) -> str:
    settings = AISettings.model_validate(config) if config is not None else AISettings()

    missing_fields: list[str] = []
    if not settings.api_key:
        missing_fields.append("API Key")
    if not settings.base_url:
        missing_fields.append("Base URL")
    if not settings.model:
        missing_fields.append("模型 ID")

    if missing_fields:
        return f"缺少 {'、'.join(missing_fields)}"
    return ""
