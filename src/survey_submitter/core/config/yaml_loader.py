"""YAML configuration loader for SurveyController."""
from __future__ import annotations

import os
from typing import Any, Dict

from survey_submitter.core.config.codec import (
    normalize_runtime_config_payload,
    serialize_runtime_config,
)
from survey_submitter.core.config.schema import RuntimeConfig


def load_yaml_config(path: str) -> RuntimeConfig:
    """Load a YAML config file and return a RuntimeConfig."""
    from yaml12 import read_yaml

    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("未提供配置文件路径")
    abs_path = os.path.abspath(raw_path)
    if not os.path.exists(abs_path):
        raise ValueError(f"配置文件不存在：{abs_path}")

    data = read_yaml(abs_path)

    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误，期望字典类型：{abs_path}")

    return normalize_runtime_config_payload(data)


def save_yaml_config(config: RuntimeConfig, path: str) -> None:
    """Save a RuntimeConfig to a YAML file."""
    from yaml12 import write_yaml

    data = serialize_runtime_config(config)
    abs_path = os.path.abspath(str(path))
    write_yaml(data, abs_path)
