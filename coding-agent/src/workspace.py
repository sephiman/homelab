"""Isolates each run in a git worktree on a local branch that is never pushed."""

import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import WorkspaceError

# Rewrites every push URL to an unreachable host for all child processes; fetch keeps working.
NO_PUSH_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "url.no-push.invalid:blocked/.pushInsteadOf",
    "GIT_CONFIG_VALUE_0": "",
}


@dataclass(frozen=True)
class Workspace:
    repo_root: Path
    worktree: Path
    branch: str
    base_commit: str
    work_dir: Path


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=os.environ | NO_PUSH_ENV,
        check=False,
    )
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _branch_name(task: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", task.lower())[:6]) or "task"
    # The random suffix keeps two runs of the same task in the same second apart.
    return f"coding-agent/{slug}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def agent_cache_dir() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache) / "coding-agent"


def has_uncommitted_changes(cwd: Path) -> bool:
    return bool(_git(cwd, "status", "--porcelain"))


def open_workspace(cwd: Path, task: str) -> Workspace:
    try:
        repo_root = Path(_git(cwd, "rev-parse", "--show-toplevel")).resolve()
    except WorkspaceError as exc:
        raise WorkspaceError(f"{cwd} is not inside a git repository") from exc

    branch = _branch_name(task)
    worktree = agent_cache_dir() / "worktrees" / repo_root.name / branch.removeprefix("coding-agent/")
    base_commit = _git(repo_root, "rev-parse", "HEAD")
    _git(repo_root, "worktree", "add", "-q", "-b", branch, str(worktree), base_commit)
    return Workspace(
        repo_root=repo_root,
        worktree=worktree,
        branch=branch,
        base_commit=base_commit,
        work_dir=worktree / cwd.resolve().relative_to(repo_root),
    )


def staged_diff(workspace: Workspace) -> str:
    # Staging first so files the agents created show up in the diff too.
    _git(workspace.worktree, "add", "-A")
    return _git(workspace.worktree, "diff", "--cached", workspace.base_commit)


def commit_and_close(workspace: Workspace, message: str) -> bool:
    """Commits whatever changed onto the branch, removes the worktree, and drops the branch if it is empty."""
    _git(workspace.worktree, "add", "-A")
    has_changes = bool(_git(workspace.worktree, "status", "--porcelain"))
    if has_changes:
        _git(workspace.worktree, "commit", "-q", "--no-verify", "-m", message)
    _git(workspace.repo_root, "worktree", "remove", "--force", str(workspace.worktree))
    if not has_changes:
        _git(workspace.repo_root, "branch", "-D", workspace.branch)
    return has_changes


def reset_to_base(workspace: Workspace) -> bool:
    """Drops every change in the worktree; True when there was something to drop."""
    if not has_uncommitted_changes(workspace.worktree):
        return False
    _git(workspace.worktree, "reset", "-q", "--hard", workspace.base_commit)
    _git(workspace.worktree, "clean", "-q", "-fd")
    return True


def discard_workspace(workspace: Workspace) -> None:
    _git(workspace.repo_root, "worktree", "remove", "--force", str(workspace.worktree))
    _git(workspace.repo_root, "branch", "-D", workspace.branch)


def branch_diff_stat(workspace: Workspace) -> str:
    return _git(workspace.repo_root, "diff", "--stat", workspace.base_commit, workspace.branch)
