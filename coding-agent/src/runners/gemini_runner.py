"""Gemini CLI runner adapter."""

from .base import BaseRunner, RunnerResult

# `gemini -p` appends its value to stdin, which carries the real prompt.
STDIN_INSTRUCTION = "Carry out the instructions above."


class GeminiRunner(BaseRunner):
    """Executes tasks headlessly via the Gemini CLI (browser OAuth session)."""

    def __init__(
        self,
        command: str = "gemini",
        default_flags: list[str] | None = None,
        plan_mode_flag: str = "--approval-mode=plan",
        exec_mode_flag: str = "--approval-mode=auto_edit",
    ):
        super().__init__(command=command, default_flags=default_flags)
        self.plan_mode_flag = plan_mode_flag
        self.exec_mode_flag = exec_mode_flag

    def run(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        read_only: bool = False,
        timeout: int | None = None,
    ) -> RunnerResult:
        if not self.is_available():
            return RunnerResult(
                stdout="",
                stderr="Gemini CLI binary ('gemini') not found in PATH. Ensure @google/gemini-cli is installed.",
                exit_code=127,
                duration_seconds=0.0,
                success=False,
            )

        cmd = [self.command, *self.default_flags]
        if model:
            cmd.extend(["-m", model])
        cmd.append(self.plan_mode_flag if read_only else self.exec_mode_flag)
        cmd.extend(["-p", STDIN_INSTRUCTION])

        return self._execute_subprocess(cmd=cmd, cwd=cwd, stdin_input=prompt, timeout=timeout)

    def interactive(self, message: str, cwd: str, model: str | None, readable_dirs: list[str]) -> int:
        cmd = [self.command, *self.default_flags]
        if model:
            cmd.extend(["-m", model])
        for directory in readable_dirs:
            cmd.extend(["--include-directories", directory])
        cmd.extend(["-i", message])
        return self._attach_terminal(cmd, cwd)
