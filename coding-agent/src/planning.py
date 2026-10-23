"""Files that carry the brief into an interactive architect session and bring the agreed plan back out."""

from dataclasses import dataclass
from pathlib import Path

from .workspace import Workspace, agent_cache_dir


@dataclass(frozen=True)
class PlanFiles:
    brief: Path
    plan: Path

    @property
    def folder(self) -> Path:
        return self.plan.parent


def plan_files_for(workspace: Workspace) -> PlanFiles:
    # Outside the worktree, so the brief and plan never end up committed and survive a discarded branch.
    folder = agent_cache_dir() / "plans"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{workspace.repo_root.name}-{workspace.branch.removeprefix('coding-agent/')}"
    return PlanFiles(brief=folder / f"{stem}.brief.md", plan=folder / f"{stem}.md")


def read_plan(files: PlanFiles) -> str | None:
    if not files.plan.is_file():
        return None
    return files.plan.read_text(encoding="utf-8").strip() or None
