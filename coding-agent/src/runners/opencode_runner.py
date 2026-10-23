"""OpenCode CLI runner adapter."""

import tempfile

from .base import BaseRunner, RunnerResult

ATTACHED_PROMPT_INSTRUCTION = "Carry out the instructions in the attached file."


class OpenCodeRunner(BaseRunner):
    """Executes tasks headlessly via OpenCode CLI; models are `provider/model`, e.g. `opencode/glm-5.3-flash-free`."""

    def __init__(self, command: str = "opencode", default_flags: list[str] | None = None):
        super().__init__(command=command, default_flags=default_flags or ["run"])

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
                stderr=(
                    "OpenCode CLI binary ('opencode') not found in PATH.\n"
                    "Install it with: sudo pacman -S opencode\n"
                    "Or switch runner in config.yaml / CLI flag (--impl-runner claude|gemini)."
                ),
                exit_code=127,
                duration_seconds=0.0,
                success=False,
            )

        cmd = [self.command, *self.default_flags, "--dir", cwd]
        if model:
            cmd.extend(["-m", model])
        if read_only:
            cmd.extend(["--agent", "plan"])

        # Prompt as an attached file: argv entries are capped at 128 KiB on Linux.
        with tempfile.NamedTemporaryFile("w", prefix="coding-agent-prompt-", suffix=".md", encoding="utf-8") as prompt_file:
            prompt_file.write(prompt)
            prompt_file.flush()
            # --file is a greedy array option, so it must come after the message.
            cmd.extend([ATTACHED_PROMPT_INSTRUCTION, "--file", prompt_file.name])
            return self._execute_subprocess(cmd=cmd, cwd=cwd, timeout=timeout)

    def interactive(self, message: str, cwd: str, model: str | None, readable_dirs: list[str]) -> int:
        # The TUI is the bare command (default_flags hold the headless `run`); it asks before
        # touching readable_dirs, which sit outside the project.
        cmd = [self.command]
        if model:
            cmd.extend(["--model", model])
        cmd.extend(["--prompt", message])
        return self._attach_terminal(cmd, cwd)
