from pathlib import Path
from zoneinfo import ZoneInfo

from agent_context.managed_config import load_timezone


def test_load_timezone_reads_the_top_level_key(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  timezone: nope\ntimezone: Europe/Madrid  # jobs follow it\n")

    assert load_timezone(config) == ZoneInfo("Europe/Madrid")


def test_load_timezone_falls_back_to_utc(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("timezone: Mars/Olympus\n")

    assert load_timezone(tmp_path / "absent.yaml") == ZoneInfo("UTC")
    assert load_timezone(tmp_path / "bad.yaml") == ZoneInfo("UTC")
