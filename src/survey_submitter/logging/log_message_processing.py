from __future__ import annotations

import logging
import re

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi_codes(text: str) -> str:
    """Remove ANSI escape codes from *text*."""
    if not text:
        return text
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def should_filter_sensitive(message: str) -> bool:
    """Return ``True`` when *message* contains sensitive token patterns."""
    if not message:
        return False
    sensitive_patterns = [
        "Authorization: Bearer ",
        "refresh_token",
        "access_token",
    ]
    return any(pattern in message for pattern in sensitive_patterns)


def determine_category(record: logging.LogRecord, message: str) -> str:
    """Derive a display category (``INFO``, ``OK``, ``WARNING``, ``ERROR``) for *record*."""
    custom_category = getattr(record, "log_category", None)
    if isinstance(custom_category, str):
        normalized = custom_category.strip().upper()
        if normalized in {"INFO", "OK", "WARNING", "ERROR"}:
            return normalized

    level = record.levelname.upper()
    if level in {"ERROR", "CRITICAL"}:
        return "ERROR"
    if level == "WARNING":
        return "WARNING"
    if level in {"OK", "SUCCESS"}:
        return "OK"

    normalized_message = message.upper()
    ok_markers = ("[OK]", "[SUCCESS]")
    ok_keywords = (
        "\u6210\u529f",
        "\u5df2\u5b8c\u6210",
        "\u89e3\u6790\u5b8c\u6210",
        "\u586b\u5199\u5b8c\u6210",
        "\u586b\u5199\u6210\u529f",
        "\u63d0\u4ea4\u6210\u529f",
        "\u4fdd\u5b58\u6210\u529f",
        "\u6062\u590d\u6210\u529f",
        "\u52a0\u8f7d\u4e0a\u6b21\u914d\u7f6e",
        "\u5df2\u52a0\u8f7d\u4e0a\u6b21\u914d\u7f6e",
        "\u52a0\u8f7d\u5b8c\u6210",
    )
    negative_keywords = (
        "\u672a\u6210\u529f",
        "\u672a\u5b8c\u6210",
        "\u5931\u8d25",
        "\u9519\u8bef",
        "\u5f02\u5e38",
    )
    if any(marker in message for marker in ok_markers):
        return "OK"
    if normalized_message.startswith("OK"):
        return "OK"
    if any(keyword in message for keyword in ok_keywords):
        if not any(neg in message for neg in negative_keywords):
            return "OK"

    return "INFO"


def apply_category_label(message: str, original_level: str, category: str) -> str:
    """Replace the original log-level label in *message* with *category*."""
    if not message or not original_level:
        return message
    original_label = f"[{original_level.upper()}]"
    replacement_label = f"[{category.upper()}]"

    deduped = _collapse_adjacent_label(message, original_label, replacement_label)
    if deduped is not None:
        return deduped

    if category.upper() == original_level.upper():
        return message
    if original_label in message:
        return message.replace(original_label, replacement_label, 1)
    return message


def _collapse_adjacent_label(message: str, original_label: str, target_label: str) -> str | None:
    """Collapse *original_label* followed by *target_label* into a single *target_label*."""
    if not message or not original_label or not target_label:
        return None
    index = message.find(original_label)
    if index == -1:
        return None
    remainder = message[index + len(original_label) :]
    trimmed = remainder.lstrip()
    if not trimmed.startswith(target_label):
        return None
    whitespace = remainder[: len(remainder) - len(trimmed)]
    suffix = trimmed[len(target_label) :]
    return f"{message[:index]}{target_label}{whitespace}{suffix}"
