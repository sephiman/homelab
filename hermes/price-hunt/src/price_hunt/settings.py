import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_context.home import hermes_home


@dataclass(frozen=True)
class Settings:
    state_dir: Path

    @property
    def db_path(self) -> Path:
        return self.state_dir / "prices.db"


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    return Settings(state_dir=hermes_home(environ) / "state")
