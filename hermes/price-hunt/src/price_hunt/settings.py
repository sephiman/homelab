import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state_dir: Path

    @property
    def db_path(self) -> Path:
        return self.state_dir / "prices.db"


def hermes_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("HERMES_HOME") or Path(environ.get("HOME", "/home/hermes")) / ".hermes")


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    home = hermes_home(environ)
    return Settings(state_dir=home / "state")
