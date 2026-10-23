"""Lightweight repository and rule context builder."""

import subprocess
from pathlib import Path
from typing import Any

from .checks import CheckResult, detect_checks


def load_rules(rules_path: str) -> str:
    """Reads coding rules from specified path or fallback."""
    p = Path(rules_path).expanduser().resolve()
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


def get_git_status(cwd: Path) -> dict[str, str]:
    """Retrieves current branch and pending changes summary."""
    status = {"branch": "unknown", "status_summary": "clean"}
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if branch.returncode == 0:
            status["branch"] = branch.stdout.strip()

        diff_stat = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if diff_stat.returncode == 0:
            changes = diff_stat.stdout.strip()
            status["status_summary"] = changes if changes else "clean"
    except (OSError, subprocess.TimeoutExpired):
        return status  # git missing or hung: the prompt just shows 'unknown'
    return status


def build_context_bundle(cwd: str, rules_path: str | None = None) -> dict[str, Any]:
    """Gathers compact project context without token bloat."""
    path = Path(cwd).expanduser().resolve()
    rules = load_rules(rules_path) if rules_path else ""
    checks = detect_checks(path)
    git_info = get_git_status(path)

    return {
        "project_name": path.name,
        "cwd": str(path),
        "rules": rules,
        "check_commands": [c.describe(path) for c in checks.checks],
        "git": git_info,
    }


def _planning_brief(task: str, context: dict[str, Any]) -> str:
    rules_section = f"\n### Coding Rules & Constraints:\n{context['rules']}\n" if context.get("rules") else ""
    check_commands = ", ".join(context["check_commands"]) or "none detected"

    return f"""### Target Project:
- Name: {context['project_name']}
- Working Directory: {context['cwd']}
- Checks run after implementation: {check_commands}
- Git Branch: {context['git']['branch']}
- Git Working Tree: {context['git']['status_summary']}
{rules_section}
### User Request:
{task}

### Required Plan Structure:
1. **Objective & Scope**: What problem does this solve?
2. **Impacted / Target Files**: Explicit file paths to create or modify.
3. **Step-by-Step Implementation**: Precise logical steps adhering strictly to the clean code rules (package by feature, immutability, guard clauses, minimal docstrings).
4. **Verification Strategy**: Specific deterministic commands (beyond the checks listed above) to verify correctness.
"""


def format_architect_prompt(task: str, context: dict[str, Any]) -> str:
    """Prepares the headless architect planning prompt."""
    return f"""You are the Lead Software Architect.
Your task is to analyze the user request and generate a clear, step-by-step Execution Plan.
DO NOT apply code modifications directly in this phase. Output ONLY the architectural plan.

{_planning_brief(task, context)}"""


def format_interactive_brief(task: str, context: dict[str, Any], plan_file: Path, revising: bool) -> str:
    """Brief for an interactive architect session; the plan comes back through plan_file."""
    revision_note = (
        f"\nThe user read your previous plan in {plan_file} and wants to revise it. Ask what should change.\n"
        if revising
        else ""
    )
    return f"""You are the Lead Software Architect, in a live conversation with the user.
Explore the codebase, ask clarifying questions and discuss trade-offs until the user agrees on a plan.
Do not modify any project files: a cheaper implementer model will do that from your plan.
{revision_note}
Plan file: {plan_file}

When the user agrees, write the complete final plan to the plan file above, following the
Required Plan Structure below, and tell the user to exit this session (/exit or Ctrl+D) to continue.
The implementer sees only that file, so it must stand on its own.

{_planning_brief(task, context)}"""


def format_implementer_prompt(task: str, plan: str, context: dict[str, Any]) -> str:
    """Prepares the implementer execution prompt."""
    rules_section = f"\n### Mandatory Coding Standards:\n{context['rules']}\n" if context.get("rules") else ""

    return f"""You are the Implementation Engineer.
Your job is to execute the approved architectural plan by making exact, clean code edits in this workspace.

### Approved Plan:
{plan}
{rules_section}
### Instructions:
- Follow the approved plan strictly.
- Create or modify the required files.
- Maintain immutability by default, guard clauses, and package-by-feature conventions.
- Minimal docstrings (at most one line, only when why is non-obvious).
- Execute the necessary file edits now.
- Never push and never commit; the orchestrator commits to a local branch.
"""


def format_verifier_prompt(failures: list[CheckResult], git_diff: str, root: Path) -> str:
    """Prepares the verifier prompt when deterministic checks fail."""
    failure_sections = "\n\n".join(
        f"#### `{failure.command.describe(root)}`\n```\n{failure.output}\n```" for failure in failures
    )
    return f"""You are the Verification Engineer.
The automated tests or linters failed after the implementation changes.
Analyze the failures and the diff, then apply the minimal surgical fix.

### Failing Checks:
{failure_sections}

### Current Diff (including new files):
{git_diff}

### Instructions:
- Identify the exact root cause of each failure.
- Modify only the necessary lines to fix the issue.
- Do not introduce unrelated changes or refactors.
- Never push and never commit; the orchestrator commits to a local branch.
"""
