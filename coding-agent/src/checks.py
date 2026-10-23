"""Detects and runs each project's own deterministic checks: tests, linters, type checkers."""

import json
import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workspace import NO_PUSH_ENV

SKIPPED_DIRS = {"node_modules", "build", "dist", "venv"}
OUTPUT_TAIL_CHARS = 20_000
UV_RUN = ("uv", "run", "--all-extras")


@dataclass(frozen=True)
class CheckCommand:
    argv: tuple[str, ...]
    cwd: Path

    def describe(self, root: Path) -> str:
        command = " ".join(self.argv)
        where = self.cwd.relative_to(root)
        return command if where == Path() else f"cd {where} && {command}"


@dataclass(frozen=True)
class CheckPlan:
    setup: tuple[CheckCommand, ...] = field(default_factory=tuple)
    checks: tuple[CheckCommand, ...] = field(default_factory=tuple)

    def __add__(self, other: "CheckPlan") -> "CheckPlan":
        return CheckPlan(setup=self.setup + other.setup, checks=self.checks + other.checks)


@dataclass(frozen=True)
class CheckResult:
    command: CheckCommand
    passed: bool
    output: str


def _project_dirs(root: Path) -> list[Path]:
    subdirs = sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIPPED_DIRS
    )
    return [root, *subdirs]


def _mypy_targets(project: Path, pyproject: dict[str, Any]) -> tuple[str, ...]:
    if "files" in pyproject.get("tool", {}).get("mypy", {}):
        return ()  # the project's own [tool.mypy] files list picks the targets
    return ("src",) if (project / "src").is_dir() else (".",)


def _python_plan(project: Path) -> CheckPlan:
    manifest = project / "pyproject.toml"
    if not manifest.exists():
        return CheckPlan()

    declared = manifest.read_text(encoding="utf-8")
    argvs: list[tuple[str, ...]] = []
    # Only tools the project itself declares, so `uv run` never fails on a missing binary.
    if "pytest" in declared:
        argvs.append((*UV_RUN, "pytest"))
    if "ruff" in declared:
        argvs += [(*UV_RUN, "ruff", "check", "."), (*UV_RUN, "ruff", "format", "--check", ".")]
    if "mypy" in declared:
        argvs.append((*UV_RUN, "mypy", *_mypy_targets(project, tomllib.loads(declared))))
    return CheckPlan(checks=tuple(CheckCommand(argv, project) for argv in argvs))


def _node_plan(project: Path) -> CheckPlan:
    manifest = project / "package.json"
    if not manifest.exists():
        return CheckPlan()

    # A fresh worktree has no node_modules; --ignore-scripts keeps husky from rewriting the shared git config.
    install = "ci" if (project / "package-lock.json").exists() else "install"
    setup = CheckCommand(("npm", install, "--no-audit", "--no-fund", "--ignore-scripts"), project)
    argvs: list[tuple[str, ...]] = []
    if (project / "tsconfig.json").exists():
        argvs.append(("npx", "tsc", "-b"))
    if "test" in json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {}):
        argvs.append(("npm", "test"))
    return CheckPlan(setup=(setup,), checks=tuple(CheckCommand(argv, project) for argv in argvs))


def _gradle_plan(project: Path) -> CheckPlan:
    if not (project / "gradlew").exists():
        return CheckPlan()
    return CheckPlan(checks=(CheckCommand(("./gradlew", "test", "--console=plain"), project),))


def detect_checks(root: Path) -> CheckPlan:
    """Looks at the root and its direct subfolders, so backend/ + frontend/ monorepos are covered."""
    plan = CheckPlan()
    for project in _project_dirs(root):
        plan = plan + _python_plan(project) + _node_plan(project) + _gradle_plan(project)
    return plan


def run_check(command: CheckCommand, timeout: int) -> CheckResult:
    try:
        proc = subprocess.run(
            list(command.argv),
            cwd=command.cwd,
            env=os.environ | NO_PUSH_ENV | {"CI": "1"},
            stdin=subprocess.DEVNULL,  # a TTY on stdin would start vitest in watch mode
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult(command, passed=False, output=f"{command.argv[0]} not found on PATH")
    except subprocess.TimeoutExpired:
        return CheckResult(command, passed=False, output=f"Timed out after {timeout} seconds")
    output = (proc.stdout + proc.stderr)[-OUTPUT_TAIL_CHARS:]
    return CheckResult(command, passed=proc.returncode == 0, output=output)
