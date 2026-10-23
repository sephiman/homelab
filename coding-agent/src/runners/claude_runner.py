"""Claude Code CLI runner adapter."""

from .base import BaseRunner, RunnerResult


class ClaudeRunner(BaseRunner):
    """Executes tasks headlessly via the Claude Code CLI (browser OAuth session)."""

    def __init__(self, command: str = "claude", default_flags: list[str] | None = None):
        super().__init__(command=command, default_flags=default_flags or ["-p"])

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
                stderr="Claude CLI binary ('claude') not found in PATH. Ensure Claude Code is installed.",
                exit_code=127,
                duration_seconds=0.0,
                success=False,
            )

        cmd = [self.command, *self.default_flags]
        if model:
            cmd.extend(["--model", model])
        if read_only:
            cmd.extend(["--tools", "Read,Grep,Glob"])
        else:
            # Explicit, so behaviour doesn't depend on the user's defaultMode; Bash stays unapproved.
            cmd.extend(["--permission-mode", "acceptEdits"])

        # Prompt on stdin: a single argv entry is capped at 128 KiB on Linux.
        return self._execute_subprocess(cmd=cmd, cwd=cwd, stdin_input=prompt, timeout=timeout)

    def interactive(self, message: str, cwd: str, model: str | None, readable_dirs: list[str]) -> int:
        # The message goes first: --add-dir and --tools are variadic and would swallow it.
        cmd = [self.command, message]
        if model:
            cmd.extend(["--model", model])
        for directory in readable_dirs:
            cmd.extend(["--add-dir", directory])
        cmd.extend(["--tools", "Read,Grep,Glob,Write,Edit"])
        return self._attach_terminal(cmd, cwd)
