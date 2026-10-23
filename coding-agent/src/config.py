"""Configuration loader and manager."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

BUNDLED_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(frozen=True)
class TierConfig:
    runner: str
    model: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    active_profile: str
    architect: TierConfig
    implementer: TierConfig
    verifier: TierConfig
    rules_file: Path
    plan_first_approval: bool = True
    auto_test_verification: bool = True
    max_verifier_iterations: int = 2
    runner_timeout_seconds: int = 3600
    runner_configs: dict[str, Any] = field(default_factory=dict)


def find_config_file(custom_path: str | None = None) -> Path:
    if custom_path:
        explicit = Path(custom_path).expanduser().resolve()
        if not explicit.is_file():
            raise ConfigError(f"Config file '{explicit}' does not exist.")
        return explicit

    candidates = [
        Path.cwd() / "config" / "config.yaml",
        Path.cwd() / "config.yaml",
        BUNDLED_DIR / "config.yaml",
        Path.home() / ".config" / "coding-agent" / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ConfigError("No config.yaml found; pass one with --config-file.")


def load_raw_config(config_path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Cannot parse {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{config_path} must be a YAML mapping.")
    return data


def _resolve_rules_path(base_dir: Path, settings: dict[str, Any], rules_override: str | None) -> Path:
    if rules_override:
        return Path(rules_override).expanduser().resolve()
    configured = (base_dir / settings.get("rules_file", "config/coding-rules.md")).resolve()
    return configured if configured.exists() else BUNDLED_DIR / "coding-rules.md"


def _tier(profile_name: str, profile: dict[str, Any], tier: str, runner: str | None, model: str | None) -> TierConfig:
    configured = profile.get(tier, {})
    resolved_runner = runner or configured.get("runner")
    if not resolved_runner:
        raise ConfigError(f"Profile '{profile_name}' has no runner for the {tier}.")
    return TierConfig(runner=resolved_runner, model=model or configured.get("model"))


def build_agent_config(
    custom_config_path: str | None = None,
    profile_override: str | None = None,
    arch_model: str | None = None,
    impl_model: str | None = None,
    verify_model: str | None = None,
    arch_runner: str | None = None,
    impl_runner: str | None = None,
    verify_runner: str | None = None,
    rules_override: str | None = None,
) -> AgentConfig:
    """Builds the active configuration merging YAML settings with runtime CLI flags."""
    cfg_path = find_config_file(custom_config_path)
    raw = load_raw_config(cfg_path)

    profiles = raw.get("profiles", {})
    active_profile = profile_override or raw.get("active_profile", "balanced")
    if active_profile not in profiles:
        available = ", ".join(profiles) or "none"
        raise ConfigError(f"Unknown profile '{active_profile}' in {cfg_path}. Available: {available}.")
    profile = profiles[active_profile]
    settings = raw.get("settings", {})

    return AgentConfig(
        active_profile=active_profile,
        architect=_tier(active_profile, profile, "architect", arch_runner, arch_model),
        implementer=_tier(active_profile, profile, "implementer", impl_runner, impl_model),
        verifier=_tier(active_profile, profile, "verifier", verify_runner, verify_model),
        rules_file=_resolve_rules_path(cfg_path.parent.parent, settings, rules_override),
        plan_first_approval=settings.get("plan_first_approval", True),
        auto_test_verification=settings.get("auto_test_verification", True),
        max_verifier_iterations=settings.get("max_verifier_iterations", 2),
        runner_timeout_seconds=settings.get("runner_timeout_seconds", 3600),
        runner_configs=raw.get("runners", {}),
    )
