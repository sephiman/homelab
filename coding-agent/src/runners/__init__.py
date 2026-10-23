"""Runner registry and factory."""

from typing import Any

from ..errors import ConfigError
from .base import BaseRunner, RunnerResult
from .claude_runner import ClaudeRunner
from .gemini_runner import GeminiRunner
from .opencode_runner import OpenCodeRunner

__all__ = ["BaseRunner", "ClaudeRunner", "GeminiRunner", "OpenCodeRunner", "RunnerResult", "get_runner"]


def get_runner(runner_name: str, runner_configs: dict[str, Any] | None = None) -> BaseRunner:
    """Instantiates a runner adapter based on its identifier."""
    runner_configs = runner_configs or {}
    cfg = runner_configs.get(runner_name, {})

    name = runner_name.lower().strip()
    command = cfg.get("command", name)
    flags = cfg.get("flags")

    if name == "claude":
        return ClaudeRunner(command=command, default_flags=flags)
    elif name == "gemini":
        plan_flag = cfg.get("plan_mode_flag", "--approval-mode=plan")
        exec_flag = cfg.get("exec_mode_flag", "--approval-mode=auto_edit")
        return GeminiRunner(
            command=command,
            default_flags=flags,
            plan_mode_flag=plan_flag,
            exec_mode_flag=exec_flag,
        )
    elif name == "opencode":
        return OpenCodeRunner(command=command, default_flags=flags)
    else:
        raise ConfigError(f"Unknown runner '{runner_name}'. Supported runners: claude, gemini, opencode.")
