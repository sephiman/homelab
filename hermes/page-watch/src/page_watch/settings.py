import os
from collections.abc import Mapping
from pathlib import Path

from agent_context.home import hermes_home


def db_path(environ: Mapping[str, str] = os.environ) -> Path:
    return hermes_home(environ) / "state" / "pages.db"
