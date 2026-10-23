"""End-to-end runs of the CLI against a throwaway git repo, with the vendor CLIs replaced by tests/fake_agent.py."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parent.parent
FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"

PACKAGE_JSON = {
    "name": "sandbox",
    "version": "1.0.0",
    "scripts": {"test": "node -e \"process.exit(require('fs').existsSync('fixed.txt') ? 0 : 1)\""},
}
PACKAGE_LOCK = {
    "name": "sandbox",
    "version": "1.0.0",
    "lockfileVersion": 3,
    "requires": True,
    "packages": {"": {"name": "sandbox", "version": "1.0.0"}},
}
CONFIG_YAML = """\
active_profile: sandbox
profiles:
  sandbox:
    architect: {runner: claude, model: opus}
    implementer: {runner: opencode, model: opencode/test-impl}
    verifier: {runner: gemini, model: flash}
settings:
  rules_file: agent-config/rules.md  # relative to the folder above config.yaml
  plan_first_approval: true
  max_verifier_iterations: 2
  runner_timeout_seconds: 60
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo = root / "repo"
        self.bin = root / "bin"
        self.config_dir = root / "agent-config"
        self.log = root / "calls.jsonl"
        self.script = root / "script.json"
        self._create_repo()
        self._create_fake_clis()
        self._create_config()

    def _create_repo(self) -> None:
        self.repo.mkdir()
        (self.repo / "package.json").write_text(json.dumps(PACKAGE_JSON))
        (self.repo / "package-lock.json").write_text(json.dumps(PACKAGE_LOCK))
        (self.repo / ".gitignore").write_text("node_modules/\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "initial")

    def _create_fake_clis(self) -> None:
        self.bin.mkdir()
        for name in ("claude", "gemini", "opencode"):
            (self.bin / name).symlink_to(FAKE_AGENT)
            # Never let a test reach the real, subscription-backed CLI further down PATH.
            resolved = shutil.which(name, path=self.env()["PATH"])
            assert resolved == str(self.bin / name), f"{name} resolves to {resolved}, not the fake"

    def _create_config(self) -> None:
        self.config_dir.mkdir()
        (self.config_dir / "config.yaml").write_text(CONFIG_YAML)
        (self.config_dir / "rules.md").write_text("Rule: guard clauses.")

    def env(self) -> dict[str, str]:
        identity = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        return os.environ | identity | {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_AGENT_LOG": str(self.log),
            "FAKE_AGENT_SCRIPT": str(self.script),
            "XDG_CACHE_HOME": str(self.root / "cache"),
        }

    def cli(
        self, *args: str, actions: list[dict[str, Any]], answers: str = "1\n", headless: bool = True
    ) -> subprocess.CompletedProcess[str]:
        self.script.write_text(json.dumps(actions))
        options = ["--config-file", str(self.config_dir / "config.yaml"), "--cwd", str(self.repo)]
        if headless:
            options.append("--headless")
        cmd = [sys.executable, "-m", "src.cli", args[0], *options, *args[1:]]
        return subprocess.run(cmd, check=False, cwd=AGENT_ROOT, env=self.env(), input=answers, capture_output=True, text=True, timeout=120)

    def worktree_count(self) -> int:
        return len(git(self.repo, "worktree", "list").splitlines())

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def agent_branches(self) -> list[str]:
        return git(self.repo, "branch", "--list", "coding-agent/*", "--format=%(refname:short)").split()


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Sandbox(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_implements_the_refined_plan_not_the_first_one(self) -> None:
        actions = [{"stdout": "PLAN-A"}, {"stdout": "PLAN-B"}, {"write": {"fixed.txt": "ok"}}]
        result = self.sandbox.cli("run", "add a feature", actions=actions, answers="2\nprefer B\n1\n")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        implementer_prompt = self.sandbox.calls()[2]["prompt"]
        self.assertIn("PLAN-B", implementer_prompt)
        self.assertNotIn("PLAN-A", implementer_prompt)

    def test_refinement_shows_the_architect_its_previous_plan(self) -> None:
        actions = [{"stdout": "PLAN-A"}, {"stdout": "PLAN-B"}]
        self.sandbox.cli("run", "add a feature", actions=actions, answers="2\nprefer B\n3\n")

        self.assertIn("PLAN-A", self.sandbox.calls()[1]["prompt"])

    def test_opencode_plans_with_its_read_only_plan_agent(self) -> None:
        result = self.sandbox.cli("plan", "a task", "--arch-runner", "opencode", actions=[{"stdout": "PLAN"}])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        argv = self.sandbox.calls()[0]["argv"]
        self.assertIn("--agent", argv)
        self.assertEqual(argv[argv.index("--agent") + 1], "plan")
        self.assertNotIn("--read-only", argv)

    def test_run_fails_when_checks_stay_red_after_all_repairs(self) -> None:
        actions = [{"stdout": "PLAN"}, {}, {}, {}]
        result = self.sandbox.cli("run", "a task", actions=actions)

        self.assertNotEqual(result.returncode, 0)
        verifier_calls = [c for c in self.sandbox.calls() if c["tool"] == "gemini"]
        self.assertEqual(len(verifier_calls), 2)

    def test_checks_rerun_after_a_repair_and_the_run_succeeds(self) -> None:
        actions = [{"stdout": "PLAN"}, {}, {"write": {"fixed.txt": "ok"}}]
        result = self.sandbox.cli("run", "a task", actions=actions)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c["tool"] for c in self.sandbox.calls()], ["claude", "opencode", "gemini"])

    def test_verifier_sees_files_the_implementer_created(self) -> None:
        actions = [{"stdout": "PLAN"}, {"write": {"new_module.py": "x = 1\n"}}, {"write": {"fixed.txt": "ok"}}]
        self.sandbox.cli("run", "a task", actions=actions)

        self.assertIn("new_module.py", self.sandbox.calls()[2]["prompt"])

    def test_prompts_larger_than_the_argv_limit_are_delivered(self) -> None:
        (self.sandbox.config_dir / "rules.md").write_text("Rule.\n" * 40_000)
        result = self.sandbox.cli("plan", "a task", actions=[{"stdout": "PLAN"}])

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(len(self.sandbox.calls()[0]["prompt"]), 200_000)

    def test_implementers_get_edit_only_permissions(self) -> None:
        claude_impl = self.sandbox.cli("run", "a task", "--impl-runner", "claude", actions=[{"stdout": "PLAN"}, {"write": {"fixed.txt": "ok"}}])
        self.assertEqual(claude_impl.returncode, 0, claude_impl.stdout + claude_impl.stderr)
        claude_argv = self.sandbox.calls()[1]["argv"]
        self.assertEqual(claude_argv[claude_argv.index("--permission-mode") + 1], "acceptEdits")

        self.sandbox.log.unlink()
        self.sandbox.cli("run", "a task", "--impl-runner", "gemini", actions=[{"stdout": "PLAN"}, {"write": {"fixed.txt": "ok"}}])
        gemini_argv = self.sandbox.calls()[1]["argv"]
        self.assertIn("--approval-mode=auto_edit", gemini_argv)
        self.assertNotIn("--approval-mode=yolo", gemini_argv)

    def test_work_lands_on_a_local_branch_and_leaves_the_checkout_alone(self) -> None:
        actions = [{"stdout": "PLAN"}, {"write": {"fixed.txt": "ok"}}]
        result = self.sandbox.cli("run", "Add the fixed marker", actions=actions)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.sandbox.repo / "fixed.txt").exists())
        self.assertEqual(git(self.sandbox.repo, "status", "--porcelain"), "")
        branches = self.sandbox.agent_branches()
        self.assertEqual(len(branches), 1)
        self.assertIn("fixed.txt", git(self.sandbox.repo, "show", "--name-only", "--format=", branches[0]))
        self.assertEqual(len(git(self.sandbox.repo, "worktree", "list").splitlines()), 1)

    def test_agents_cannot_push(self) -> None:
        actions = [{"stdout": "PLAN"}, {"write": {"fixed.txt": "ok"}}]
        self.sandbox.cli("run", "a task", actions=actions)

        implementer = self.sandbox.calls()[1]
        self.assertNotEqual(Path(implementer["cwd"]).resolve(), self.sandbox.repo.resolve())
        self.assertIn("pushInsteadOf", implementer["push_guard"])

    def test_unknown_profile_is_an_error(self) -> None:
        result = self.sandbox.cli("plan", "a task", "--profile", "nope", actions=[])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope", result.stdout + result.stderr)
        self.assertEqual(self.sandbox.calls(), [])

    def test_malformed_config_is_an_error(self) -> None:
        (self.sandbox.config_dir / "config.yaml").write_text("profiles: [unclosed\n")
        result = self.sandbox.cli("plan", "a task", actions=[])

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sandbox.calls(), [])


class InteractivePlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sandbox = Sandbox(Path(self._tmp.name))
        self.task_file = Path(self._tmp.name) / "task.md"
        self.task_file.write_text("Long brief:\n" + "- requirement\n" * 50)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_interactive(self, actions: list[dict[str, Any]], answers: str) -> subprocess.CompletedProcess[str]:
        return self.sandbox.cli("run", "--task-file", str(self.task_file), actions=actions, answers=answers, headless=False)

    def test_run_chats_with_the_architect_and_implements_the_plan_it_wrote(self) -> None:
        result = self.run_interactive([{"write_plan": "PLAN-X"}, {"write": {"fixed.txt": "ok"}}], answers="1\n")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        architect, implementer = self.sandbox.calls()
        self.assertTrue(architect["interactive"])
        self.assertIn("- requirement", architect["prompt"])
        self.assertIn("PLAN-X", implementer["prompt"])
        self.assertEqual(len(self.sandbox.agent_branches()), 1)

    def test_going_back_to_the_architect_implements_the_revised_plan(self) -> None:
        actions = [{"write_plan": "PLAN-V1"}, {"write_plan": "PLAN-V2"}, {"write": {"fixed.txt": "ok"}}]
        result = self.run_interactive(actions, answers="2\n1\n")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.sandbox.calls()
        self.assertIn("revise", calls[1]["prompt"].lower())
        self.assertIn("PLAN-V2", calls[2]["prompt"])
        self.assertNotIn("PLAN-V1", calls[2]["prompt"])

    def test_stray_architect_edits_are_dropped_before_implementing(self) -> None:
        actions = [{"write_plan": "PLAN", "write": {"stray.txt": "x"}}, {"write": {"fixed.txt": "ok"}}]
        result = self.run_interactive(actions, answers="1\n")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        committed = git(self.sandbox.repo, "show", "--name-only", "--format=", self.sandbox.agent_branches()[0])
        self.assertIn("fixed.txt", committed)
        self.assertNotIn("stray.txt", committed)

    def test_aborting_without_a_plan_leaves_no_branch_or_worktree(self) -> None:
        result = self.run_interactive([{}], answers="3\n")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sandbox.agent_branches(), [])
        self.assertEqual(self.sandbox.worktree_count(), 1)

    def test_architect_chats_inside_the_throwaway_worktree(self) -> None:
        self.run_interactive([{"write_plan": "PLAN"}], answers="3\n")

        self.assertNotEqual(Path(self.sandbox.calls()[0]["cwd"]).resolve(), self.sandbox.repo.resolve())

    def test_plan_command_prints_the_agreed_plan_and_cleans_up(self) -> None:
        result = self.sandbox.cli(
            "plan", "--task-file", str(self.task_file), actions=[{"write_plan": "PLAN-ONLY"}], headless=False
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PLAN-ONLY", result.stdout)
        self.assertEqual(self.sandbox.agent_branches(), [])
        self.assertEqual(self.sandbox.worktree_count(), 1)

    def test_task_needs_text_or_a_file_but_not_both(self) -> None:
        neither = self.sandbox.cli("run", actions=[])
        both = self.sandbox.cli("run", "inline", "--task-file", str(self.task_file), actions=[])

        self.assertNotEqual(neither.returncode, 0)
        self.assertNotEqual(both.returncode, 0)
        self.assertEqual(self.sandbox.calls(), [])


if __name__ == "__main__":
    unittest.main()
