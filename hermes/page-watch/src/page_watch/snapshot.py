import hashlib
from collections.abc import Sequence

# Lines kept around each anchor: enough for a "Tickets: on sale" row next to the event's name.
_CONTEXT_LINES = 3


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def focus(lines: Sequence[str], anchors: Sequence[str]) -> tuple[list[str], bool]:
    """The lines around *anchors*, or every line (and False) when no anchor is on the page."""
    wanted = [_folded(anchor) for anchor in anchors if anchor.strip()]
    hits = [index for index, line in enumerate(lines) if any(a in _folded(line) for a in wanted)]
    if not hits:
        return list(lines), False
    keep: set[int] = set()
    for index in hits:
        keep.update(range(max(0, index - _CONTEXT_LINES), min(len(lines), index + _CONTEXT_LINES + 1)))
    return [lines[index] for index in sorted(keep)], True


def fingerprint(lines: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(_folded(line) for line in lines).encode()).hexdigest()


def phrases_on(lines: Sequence[str], phrases: Sequence[str]) -> tuple[str, ...]:
    """The *phrases* that appear in *lines*, ignoring case and spacing."""
    text = _folded(" ".join(lines))
    return tuple(phrase for phrase in phrases if phrase.strip() and _folded(phrase) in text)


def excerpt(lines: Sequence[str], max_chars: int) -> str:
    text = "\n".join(lines)
    return text if len(text) <= max_chars else f"{text[:max_chars]}\n[…cut]"
