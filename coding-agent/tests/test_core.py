"""Unit tests for coding-agent core modules using standard library unittest."""

import sys
import tempfile
import unittest
from pathlib import Path

from src.checks import detect_checks
from src.config import TierConfig, build_agent_config
from src.context import build_context_bundle, format_architect_prompt
from src.runners import ClaudeRunner, GeminiRunner, OpenCodeRunner, get_runner


class TestCodingAgentCore(unittest.TestCase):

    def test_runner_factory(self):
        claude = get_runner("claude")
        self.assertIsInstance(claude, ClaudeRunner)
        self.assertEqual(claude.command, "claude")

        gemini = get_runner("gemini")
        self.assertIsInstance(gemini, GeminiRunner)
        self.assertEqual(gemini.command, "gemini")

        opencode = get_runner("opencode")
        self.assertIsInstance(opencode, OpenCodeRunner)
        self.assertEqual(opencode.command, "opencode")

    def test_config_builder_overrides(self):
        cfg = build_agent_config(
            profile_override="balanced",
            arch_model="custom-arch-model-1",
            impl_model="custom-impl-model-2",
            verify_model="custom-verify-model-3",
            arch_runner="gemini",
            impl_runner="claude",
            verify_runner="opencode",
        )
        self.assertEqual(cfg.active_profile, "balanced")
        self.assertEqual(cfg.architect, TierConfig(runner="gemini", model="custom-arch-model-1"))
        self.assertEqual(cfg.implementer, TierConfig(runner="claude", model="custom-impl-model-2"))
        self.assertEqual(cfg.verifier, TierConfig(runner="opencode", model="custom-verify-model-3"))

    def test_context_and_prompt_formatting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            rules_file = tmp_path / "rules.md"
            rules_file.write_text("Rule 1: Immutability by default.", encoding="utf-8")

            bundle = build_context_bundle(cwd=str(tmp_path), rules_path=str(rules_file))
            self.assertEqual(bundle["project_name"], tmp_path.name)
            self.assertIn("Rule 1", bundle["rules"])

            prompt = format_architect_prompt("Refactor authentication module", bundle)
            self.assertIn("Lead Software Architect", prompt)
            self.assertIn("Refactor authentication module", prompt)
            self.assertIn("Rule 1: Immutability by default", prompt)

    def test_timeout_keeps_partial_output_as_text(self):
        script = "import time; print('partial', flush=True); time.sleep(5)"
        result = ClaudeRunner()._execute_subprocess(cmd=[sys.executable, "-c", script], cwd=".", timeout=1)
        self.assertFalse(result.success)
        self.assertEqual(result.stdout, "partial\n")

    def test_detects_checks_in_backend_and_frontend_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "backend").mkdir()
            (root / "backend" / "pyproject.toml").write_text("[tool.ruff]\n[tool.mypy]\n[dependency-groups]\ndev = ['pytest']\n")
            (root / "backend" / "src").mkdir()
            (root / "frontend").mkdir()
            (root / "frontend" / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
            (root / "frontend" / "package-lock.json").write_text("{}")
            (root / "frontend" / "tsconfig.json").write_text("{}")
            (root / "frontend" / "node_modules" / "dep").mkdir(parents=True)
            (root / "frontend" / "node_modules" / "dep" / "package.json").write_text("{}")

            plan = detect_checks(root)

            commands = {(c.cwd.name, " ".join(c.argv)) for c in plan.checks}
            self.assertIn(("backend", "uv run --all-extras pytest"), commands)
            self.assertIn(("backend", "uv run --all-extras ruff check ."), commands)
            self.assertIn(("backend", "uv run --all-extras mypy src"), commands)
            self.assertIn(("frontend", "npx tsc -b"), commands)
            self.assertIn(("frontend", "npm test"), commands)
            self.assertEqual([" ".join(c.argv) for c in plan.setup], ["npm ci --no-audit --no-fund --ignore-scripts"])
            self.assertNotIn("dep", {c.cwd.name for c in plan.checks})


if __name__ == "__main__":
    unittest.main()
