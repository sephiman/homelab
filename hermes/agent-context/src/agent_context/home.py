import os
from collections.abc import Mapping
from pathlib import Path


def hermes_home(environ: Mapping[str, str] = os.environ) -> Path:
    return Path(environ.get("HERMES_HOME") or Path(environ.get("HOME", "/home/hermes")) / ".hermes")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values
