#!/usr/bin/env python3
"""Stand-in for the claude/gemini/opencode CLIs: logs each call and replays one scripted action."""

import json
import os
import re
import sys
from pathlib import Path


def attached_files(argv: list[str]) -> str:
    return "".join(Path(argv[i + 1]).read_text() for i, arg in enumerate(argv[:-1]) if arg == "--file")


def is_interactive(tool: str, argv: list[str]) -> bool:
    if tool == "claude":
        return "-p" not in argv
    if tool == "gemini":
        return "-i" in argv
    return argv[:1] != ["run"]


def brief_in(argv: list[str]) -> str:
    match = re.search(r"(\S+\.brief\.md)", " ".join(argv))
    return Path(match.group(1)).read_text() if match else ""


log = Path(os.environ["FAKE_AGENT_LOG"])
calls_so_far = len(log.read_text().splitlines()) if log.exists() else 0
script = json.loads(Path(os.environ["FAKE_AGENT_SCRIPT"]).read_text())
action = script[calls_so_far] if calls_so_far < len(script) else {}

tool = Path(sys.argv[0]).name
argv = sys.argv[1:]
interactive = is_interactive(tool, argv)
# An interactive session owns the terminal; reading stdin here would swallow the gate answers.
stdin = "" if interactive or sys.stdin.isatty() else sys.stdin.read()
brief = brief_in(argv)
call = {
    "tool": tool,
    "argv": argv,
    "interactive": interactive,
    "prompt": stdin + attached_files(argv) + brief,
    "cwd": os.getcwd(),
    "push_guard": os.environ.get("GIT_CONFIG_KEY_0", ""),
}
with log.open("a") as f:
    f.write(json.dumps(call) + "\n")

for relative_path, content in action.get("write", {}).items():
    Path(relative_path).write_text(content)
if "write_plan" in action:
    plan_file = re.search(r"Plan file: (\S+)", brief)
    assert plan_file, "brief names no plan file"
    Path(plan_file.group(1)).write_text(action["write_plan"])
sys.stdout.write(action.get("stdout", ""))
sys.exit(action.get("exit", 0))
