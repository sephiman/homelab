import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id             INTEGER PRIMARY KEY,
    chat_id        TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    condition      TEXT    NOT NULL,
    every_minutes  INTEGER NOT NULL,
    starts_at      TEXT,
    anchors        TEXT    NOT NULL,
    waiting        TEXT    NOT NULL,
    ready          TEXT    NOT NULL,
    fingerprint    TEXT,
    summary        TEXT    NOT NULL,
    checked_at     TEXT,
    met_at         TEXT,
    failures       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    noisy_reported INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    watch_id    INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    fingerprint TEXT    NOT NULL,
    met         INTEGER NOT NULL,
    summary     TEXT    NOT NULL,
    PRIMARY KEY (watch_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS model_calls (
    watch_id  INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    called_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id      INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    text    TEXT NOT NULL,
    due_at  TEXT NOT NULL
);
"""

_WATCH_COLUMNS = (
    "id, chat_id, url, condition, every_minutes, starts_at, anchors, waiting, ready, fingerprint,"
    " summary, checked_at, met_at, failures, last_error, noisy_reported"
)


@dataclass(frozen=True)
class PageWatch:
    id: int
    chat_id: str
    url: str
    condition: str
    every: timedelta
    starts_at: datetime | None
    anchors: tuple[str, ...]
    waiting: tuple[str, ...]
    ready: tuple[str, ...]
    fingerprint: str | None
    summary: str
    checked_at: datetime | None = None
    met_at: datetime | None = None
    failures: int = 0
    last_error: str | None = None
    noisy_reported: bool = False


@dataclass(frozen=True)
class Verdict:
    met: bool
    summary: str


@dataclass(frozen=True)
class Reminder:
    id: int
    chat_id: str
    text: str
    due_at: datetime


def _time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _phrases(value: object) -> tuple[str, ...]:
    loaded = json.loads(value) if isinstance(value, str) else []
    return tuple(item for item in loaded if isinstance(item, str))


def _watch(row: tuple[object, ...]) -> PageWatch:
    (id_, chat_id, url, condition, every, starts, anchors, waiting, ready, fp, summary, checked, met,
     failures, last_error, noisy) = row  # fmt: skip
    assert isinstance(id_, int) and isinstance(every, int) and isinstance(failures, int)
    assert isinstance(chat_id, str) and isinstance(url, str) and isinstance(condition, str)
    assert isinstance(summary, str)
    assert fp is None or isinstance(fp, str)
    assert last_error is None or isinstance(last_error, str)
    return PageWatch(
        id=id_,
        chat_id=chat_id,
        url=url,
        condition=condition,
        every=timedelta(minutes=every),
        starts_at=_time(starts),
        anchors=_phrases(anchors),
        waiting=_phrases(waiting),
        ready=_phrases(ready),
        fingerprint=fp,
        summary=summary,
        checked_at=_time(checked),
        met_at=_time(met),
        failures=failures,
        last_error=last_error,
        noisy_reported=bool(noisy),
    )


class WatchStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connection() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def add(self, watch: PageWatch, now: datetime) -> PageWatch:
        """Inserts *watch* (its id is ignored) and returns it with the id it got."""
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO watches (chat_id, url, condition, every_minutes, anchors, waiting, ready,"
                " summary, created_at) VALUES (?, ?, ?, ?, '[]', '[]', '[]', '', ?)",
                (watch.chat_id, watch.url, watch.condition, 0, now.isoformat()),
            )
            watch_id = cursor.lastrowid
        assert watch_id is not None
        saved = replace(watch, id=watch_id)
        self.save(saved)
        return saved

    def save(self, watch: PageWatch) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE watches SET every_minutes = ?, starts_at = ?, anchors = ?, waiting = ?, ready = ?,"
                " fingerprint = ?, summary = ?, checked_at = ?, met_at = ?, failures = ?, last_error = ?,"
                " noisy_reported = ? WHERE id = ?",
                (
                    int(watch.every.total_seconds() // 60),
                    _iso(watch.starts_at),
                    json.dumps(watch.anchors),
                    json.dumps(watch.waiting),
                    json.dumps(watch.ready),
                    watch.fingerprint,
                    watch.summary,
                    _iso(watch.checked_at),
                    _iso(watch.met_at),
                    watch.failures,
                    watch.last_error,
                    watch.noisy_reported,
                    watch.id,
                ),
            )

    def watches(self, chat_id: str | None = None) -> list[PageWatch]:
        """Every watch, or only those of *chat_id*."""
        with self._connection() as db:
            rows = db.execute(
                f"SELECT {_WATCH_COLUMNS} FROM watches WHERE ? IS NULL OR chat_id = ? ORDER BY id",
                (chat_id, chat_id),
            ).fetchall()
        return [_watch(row) for row in rows]

    def remove(self, watch_id: int) -> bool:
        with self._connection() as db:
            return db.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount > 0

    def verdict(self, watch_id: int, fingerprint: str) -> Verdict | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT met, summary FROM verdicts WHERE watch_id = ? AND fingerprint = ?",
                (watch_id, fingerprint),
            ).fetchone()
        return Verdict(bool(row[0]), row[1]) if row else None

    def save_verdict(self, watch_id: int, fingerprint: str, verdict: Verdict) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO verdicts (watch_id, fingerprint, met, summary) VALUES (?, ?, ?, ?)",
                (watch_id, fingerprint, verdict.met, verdict.summary),
            )

    def record_model_call(self, watch_id: int, now: datetime) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO model_calls (watch_id, called_at) VALUES (?, ?)", (watch_id, now.isoformat())
            )

    def model_calls_since(self, watch_id: int, since: datetime) -> int:
        with self._connection() as db:
            row = db.execute(
                "SELECT count(*) FROM model_calls WHERE watch_id = ? AND called_at >= ?",
                (watch_id, since.isoformat()),
            ).fetchone()
        return int(row[0])

    def prune_model_calls(self, before: datetime) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM model_calls WHERE called_at < ?", (before.isoformat(),))

    def add_reminder(self, chat_id: str, text: str, due_at: datetime) -> Reminder:
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO reminders (chat_id, text, due_at) VALUES (?, ?, ?)",
                (chat_id, text, due_at.isoformat()),
            )
            reminder_id = cursor.lastrowid
        assert reminder_id is not None
        return Reminder(reminder_id, chat_id, text, due_at)

    def reminders(self, chat_id: str | None = None) -> list[Reminder]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, chat_id, text, due_at FROM reminders WHERE ? IS NULL OR chat_id = ?"
                " ORDER BY due_at, id",
                (chat_id, chat_id),
            ).fetchall()
        return [Reminder(row[0], row[1], row[2], datetime.fromisoformat(row[3])) for row in rows]

    def remove_reminder(self, reminder_id: int) -> bool:
        with self._connection() as db:
            return db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0
