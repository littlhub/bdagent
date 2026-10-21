"""配置管理 - 全局与项目两级配置，后者覆盖前者。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "",
    "base_url": "https://apihub.agnes-ai.com/v1",
    "temperature": 0.2,
    "max_steps": 200,
    "max_loop_iterations": 100,
    "exec_timeout": 120,
    "max_output_chars": 20000,
    "sandbox_dir": "/tmp/dbagent-sandbox",
    "agent_writable": True,
    "agent_root": "",
    "agent_max_rounds": 24,
}


@dataclass
class Settings:
    """运行时配置。"""

    model: str = ""
    base_url: str = DEFAULT_SETTINGS["base_url"]
    temperature: float = 0.2
    max_steps: int = 200
    max_loop_iterations: int = 100
    exec_timeout: int = 120
    max_output_chars: int = 20000
    sandbox_dir: str = "/tmp/dbagent-sandbox"
    agent_writable: bool = True
    agent_root: str = ""
    agent_max_rounds: int = 24
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(data.pop("extra", {}) or {})
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        return cls(**{k: v for k, v in data.items() if k in known},
                   extra={k: v for k, v in data.items() if k not in known})


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """递归合并，overrides 优先。"""
    result = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def default_paths(cwd: str | None = None) -> tuple[str, str]:
    """返回 (全局配置路径, 项目配置路径)。"""
    home = os.path.expanduser("~")
    global_path = os.path.join(home, ".dbagent", "settings.json")
    project_root = cwd or os.getcwd()
    project_path = os.path.join(project_root, ".dbagent", "settings.json")
    return global_path, project_path


def load_settings(cwd: str | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    """按 全局 < 项目 < 显式覆盖 的顺序合并配置。"""
    global_path, project_path = default_paths(cwd)
    merged = deep_merge(DEFAULT_SETTINGS, _read_json(global_path))
    merged = deep_merge(merged, _read_json(project_path))
    if overrides:
        merged = deep_merge(merged, overrides)
    return Settings.from_dict(merged)


def save_settings(settings: Settings, scope: str = "global",
                  cwd: str | None = None) -> str:
    """保存配置，scope 为 global 或 project，返回写入路径。"""
    global_path, project_path = default_paths(cwd)
    path = project_path if scope == "project" else global_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
