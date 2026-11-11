import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id                  INTEGER PRIMARY KEY,
    product_id          TEXT    NOT NULL,
    title               TEXT    NOT NULL,
    url                 TEXT    NOT NULL,
    target_cents        INTEGER NOT NULL,
    created_at          TEXT    NOT NULL,
    notified            INTEGER NOT NULL DEFAULT 0,
    available           INTEGER NOT NULL DEFAULT 1,
    last_notified_cents INTEGER
);
CREATE TABLE IF NOT EXISTS price_points (
    watch_id    INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    checked_at  TEXT    NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS price_points_by_watch ON price_points (watch_id, checked_at);
CREATE TABLE IF NOT EXISTS check_status (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS watch_candidates (
    watch_id   INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    product_id TEXT    NOT NULL,
    is_match   INTEGER NOT NULL,
    PRIMARY KEY (watch_id, product_id)
);
"""


@dataclass(frozen=True)
class Watch:
    id: int
    product_id: str
    title: str
    url: str
    target_cents: int
    notified: bool
    available: bool
    last_notified_cents: int | None


@dataclass(frozen=True)
class PriceSummary:
    latest_cents: int | None
    lowest_cents: int | None


class WatchStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connection() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(watches)")}
            if "last_notified_cents" not in columns:
                db.execute("ALTER TABLE watches ADD COLUMN last_notified_cents INTEGER")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def add(self, product_id: str, title: str, url: str, target_cents: int, now: datetime) -> Watch:
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO watches (product_id, title, url, target_cents, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (product_id, title, url, target_cents, now.isoformat()),
            )
            watch_id = cursor.lastrowid
        assert watch_id is not None
        return Watch(watch_id, product_id, title, url, target_cents, False, True, None)

    def all(self) -> list[Watch]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, product_id, title, url, target_cents, notified, available,"
                " last_notified_cents FROM watches ORDER BY id"
            ).fetchall()
        return [
            Watch(row[0], row[1], row[2], row[3], row[4], bool(row[5]), bool(row[6]), row[7]) for row in rows
        ]

    def remove(self, watch_id: int) -> bool:
        with self._connection() as db:
            return db.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount > 0

    def record_price(self, watch_id: int, price_cents: int, now: datetime) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO price_points (watch_id, checked_at, price_cents) VALUES (?, ?, ?)",
                (watch_id, now.isoformat(), price_cents),
            )

    def update_state(self, watch_id: int, notified: bool, available: bool) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE watches SET notified = ?, available = ? WHERE id = ?", (notified, available, watch_id)
            )

    def set_last_notified(self, watch_id: int, price_cents: int) -> None:
        with self._connection() as db:
            db.execute("UPDATE watches SET last_notified_cents = ? WHERE id = ?", (price_cents, watch_id))

    def price_summary(self, watch_id: int, since: datetime) -> PriceSummary:
        with self._connection() as db:
            latest = db.execute(
                "SELECT price_cents FROM price_points WHERE watch_id = ? ORDER BY checked_at DESC LIMIT 1",
                (watch_id,),
            ).fetchone()
            lowest = db.execute(
                "SELECT min(price_cents) FROM price_points WHERE watch_id = ? AND checked_at >= ?",
                (watch_id, since.isoformat()),
            ).fetchone()
        return PriceSummary(latest[0] if latest else None, lowest[0] if lowest else None)

    def verdicts(self, watch_id: int) -> dict[str, bool]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT product_id, is_match FROM watch_candidates WHERE watch_id = ?", (watch_id,)
            ).fetchall()
        return {product_id: bool(is_match) for product_id, is_match in rows}

    def save_verdicts(self, watch_id: int, verdicts: Mapping[str, bool]) -> None:
        with self._connection() as db:
            db.executemany(
                "INSERT INTO watch_candidates (watch_id, product_id, is_match) VALUES (?, ?, ?)"
                " ON CONFLICT (watch_id, product_id) DO UPDATE SET is_match = excluded.is_match",
                [(watch_id, product_id, is_match) for product_id, is_match in verdicts.items()],
            )

    def last_error(self) -> str | None:
        with self._connection() as db:
            row = db.execute("SELECT last_error FROM check_status WHERE id = 1").fetchone()
        return row[0] if row else None

    def set_last_error(self, error: str | None) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO check_status (id, last_error) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET last_error = excluded.last_error",
                (error,),
            )
