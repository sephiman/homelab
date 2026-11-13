from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from page_watch.errors import InvalidInputError
from page_watch.page import normalize_url, visible_lines
from page_watch.snapshot import focus
from page_watch.timing import format_every, parse_every, parse_local_time


def test_visible_lines_keep_block_text_and_drop_scripts_and_styles() -> None:
    html = (
        "<html><head><title>Shop</title><style>p{}</style></head><body>"
        "<div>Tickets <b>on</b>\n sale</div><script>buy()</script><noscript>Enable JS</noscript>"
        "<ul><li>One</li><li>Two &amp; three</li></ul></body></html>"
    )

    assert visible_lines(html) == ["Shop", "Tickets on sale", "One", "Two & three"]


def test_focus_keeps_the_lines_around_the_anchor_or_everything_without_one() -> None:
    lines = [f"line {n}" for n in range(20)]
    lines[10] = "THE BAND - 14 March"

    near, found = focus(lines, ["the band - 14 march"])
    everything, missing = focus(lines, ["Other act"])

    assert found and near == lines[7:14]
    assert not missing and everything == lines


@pytest.mark.parametrize(("text", "expected"), [("5m", 5), ("30min", 30), ("1h", 60), ("2d", 2880)])
def test_parse_every(text: str, expected: int) -> None:
    every = parse_every(text)

    assert every == timedelta(minutes=expected)
    assert parse_every(format_every(every)) == every


@pytest.mark.parametrize("text", ["1m", "8d", "hourly"])
def test_parse_every_rejects_out_of_range_or_unreadable(text: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_every(text)


def test_parse_local_time_uses_the_given_zone() -> None:
    madrid = ZoneInfo("Europe/Madrid")

    assert parse_local_time("2026-12-12 09:55", madrid).astimezone(UTC) == datetime(
        2026, 12, 12, 8, 55, tzinfo=UTC
    )
    assert parse_local_time("2026-12-12", madrid).hour == 0


def test_normalize_url_needs_a_web_link() -> None:
    assert normalize_url(" www.venue.nl/agenda ") == "https://www.venue.nl/agenda"
    with pytest.raises(InvalidInputError):
        normalize_url("file:///etc/passwd")
