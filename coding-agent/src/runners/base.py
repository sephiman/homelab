"""Base runner interface for all LLM subprocess execution adapters."""

import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..workspace import NO_PUSH_ENV


@dataclass(frozen=True)
class RunnerResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    success: bool


def _as_text(output: str | bytes | None) -> str:
    # TimeoutExpired carries bytes even when the process ran with text=True.
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output or ""


class BaseRunner(ABC):
    """Abstract base class for headless CLI tool runners."""

    def __init__(self, command: str, default_flags: list[str] | None = None):
        self.command = command
        self.default_flags = default_flags or []

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def resolve_path(self) -> str | None:
        return shutil.which(self.command)

    @abstractmethod
    def run(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        read_only: bool = False,
        timeout: int | None = None,
    ) -> RunnerResult:
        pass

    @abstractmethod
    def interactive(self, message: str, cwd: str, model: str | None, readable_dirs: list[str]) -> int:
        """Opens the CLI's own chat UI on this terminal with message as the first turn; returns its exit code."""

    def _attach_terminal(self, cmd: list[str], cwd: str) -> int:
        # A Python handler (not SIG_IGN) is reset on exec, so Ctrl+C still reaches the chat UI but not us.
        previous = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            return subprocess.run(cmd, cwd=cwd, env=os.environ | NO_PUSH_ENV, check=False).returncode
        except OSError:
            return 127
        finally:
            signal.signal(signal.SIGINT, previous)

    def _execute_subprocess(
        self,
        cmd: list[str],
        cwd: str,
        stdin_input: str | None = None,
        timeout: int | None = None,
    ) -> RunnerResult:
        start_time = time.monotonic()

        def elapsed() -> float:
            return round(time.monotonic() - start_time, 2)

        try:
            process = subprocess.run(
                cmd,
                cwd=cwd,
                # Always pipe stdin: inheriting the terminal would let the CLI eat the approval-gate answers.
                input=stdin_input or "",
                text=True,
                capture_output=True,
                timeout=timeout,
                env=os.environ | NO_PUSH_ENV,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return RunnerResult(
                stdout=_as_text(exc.stdout),
                stderr=f"Command timed out after {timeout} seconds",
                exit_code=124,
                duration_seconds=elapsed(),
                success=False,
            )
        except OSError as exc:
            return RunnerResult(stdout="", stderr=str(exc), exit_code=1, duration_seconds=elapsed(), success=False)

        return RunnerResult(
            stdout=process.stdout,
            stderr=process.stderr,
            exit_code=process.returncode,
            duration_seconds=elapsed(),
            success=process.returncode == 0,
        )
