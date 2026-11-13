from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MANAGED_CONFIG = Path("/etc/hermes/config.yaml")


def read_managed_config(path: Path = MANAGED_CONFIG) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def section_values(text: str, section: str) -> dict[str, str]:
    """The scalar keys of a top-level block (``model:``) of Hermes' config.yaml."""
    values: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            inside = line.strip() == f"{section}:"
            continue
        if not inside:
            continue
        key, sep, value = line.strip().partition(":")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def top_level_value(text: str, key: str) -> str | None:
    for line in text.splitlines():
        if line[:1].isspace() or line.lstrip().startswith("#"):
            continue
        name, sep, value = line.partition(":")
        if sep and name.strip() == key and value.strip():
            return value.split("#", 1)[0].strip().strip("\"'")
    return None


def load_timezone(path: Path = MANAGED_CONFIG) -> ZoneInfo:
    """Hermes' ``timezone:``, which its scheduled jobs follow; UTC when unset or unknown."""
    text = read_managed_config(path)
    name = top_level_value(text, "timezone") if text else None
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
