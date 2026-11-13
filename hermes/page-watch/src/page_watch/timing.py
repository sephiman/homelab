import re
from datetime import datetime, timedelta, tzinfo

from page_watch.errors import InvalidInputError

MIN_EVERY = timedelta(minutes=5)
MAX_EVERY = timedelta(days=7)
_EVERY = re.compile(r"(\d+)\s*(m|min|h|d)")
_UNITS = {"m": "minutes", "min": "minutes", "h": "hours", "d": "days"}
_LOCAL_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d")


def parse_every(text: str) -> timedelta:
    """'5m', '30min', '1h', '1d'; the scheduled job runs every 5 minutes, so that is the floor."""
    match = _EVERY.fullmatch(text.strip().lower())
    if not match:
        raise InvalidInputError(f"not an interval like 5m, 1h or 1d: {text!r}")
    every = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    if not MIN_EVERY <= every <= MAX_EVERY:
        raise InvalidInputError("the interval must be between 5m and 7d")
    return every


def parse_local_time(text: str, zone: tzinfo) -> datetime:
    """'2026-12-12 09:55' or '2026-12-12' (midnight) in Hermes' timezone."""
    for layout in _LOCAL_FORMATS:
        try:
            return datetime.strptime(text.strip(), layout).replace(tzinfo=zone)
        except ValueError:
            continue
    raise InvalidInputError(f"not a date like 2026-12-12 or 2026-12-12 09:55: {text!r}")


def format_every(every: timedelta) -> str:
    minutes = int(every.total_seconds() // 60)
    if minutes % (24 * 60) == 0:
        return f"{minutes // (24 * 60)}d"
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def format_local(moment: datetime, zone: tzinfo) -> str:
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M")
