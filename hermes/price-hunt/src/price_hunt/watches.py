import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    last_notified_cents INTEGER,
    min_drop_cents      INTEGER,
    min_drop_bps        INTEGER
);
CREATE TABLE IF NOT EXISTS price_points (
    watch_id    INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    checked_at  TEXT    NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS price_points_by_watch ON price_points (watch_id, checked_at);
CREATE TABLE IF NOT EXISTS check_status (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_error  TEXT,
    reported_at TEXT
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
    min_drop_cents: int | None = None
    min_drop_bps: int | None = None


@dataclass(frozen=True)
class PriceSummary:
    latest_cents: int | None
    lowest_cents: int | None


@dataclass(frozen=True)
class PriceStats:
    count: int
    latest_cents: int | None
    lowest_cents: int | None
    highest_cents: int | None


class WatchStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        with self._connection() as db:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(watches)")}
            if "last_notified_cents" not in columns:
                db.execute("ALTER TABLE watches ADD COLUMN last_notified_cents INTEGER")
            if "min_drop_cents" not in columns:
                db.execute("ALTER TABLE watches ADD COLUMN min_drop_cents INTEGER")
            if "min_drop_bps" not in columns:
                db.execute("ALTER TABLE watches ADD COLUMN min_drop_bps INTEGER")
            status_cols = {row[1] for row in db.execute("PRAGMA table_info(check_status)")}
            if "reported_at" not in status_cols:
                db.execute("ALTER TABLE check_status ADD COLUMN reported_at TEXT")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._db_path)) as db, db:
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def add(
        self,
        product_id: str,
        title: str,
        url: str,
        target_cents: int,
        now: datetime,
        min_drop_cents: int | None = None,
        min_drop_bps: int | None = None,
    ) -> Watch:
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO watches (product_id, title, url, target_cents, created_at,"
                " min_drop_cents, min_drop_bps) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (product_id, title, url, target_cents, now.isoformat(), min_drop_cents, min_drop_bps),
            )
            watch_id = cursor.lastrowid
        assert watch_id is not None
        return Watch(
            watch_id,
            product_id,
            title,
            url,
            target_cents,
            False,
            True,
            None,
            min_drop_cents,
            min_drop_bps,
        )

    @staticmethod
    def _row_to_watch(row: tuple[object, ...]) -> Watch:
        assert isinstance(row[0], int)
        assert isinstance(row[1], str)
        assert isinstance(row[2], str)
        assert isinstance(row[3], str)
        assert isinstance(row[4], int)
        assert row[7] is None or isinstance(row[7], int)
        assert row[8] is None or isinstance(row[8], int)
        assert row[9] is None or isinstance(row[9], int)
        return Watch(
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            bool(row[5]),
            bool(row[6]),
            row[7],
            row[8],
            row[9],
        )

    def all(self) -> list[Watch]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, product_id, title, url, target_cents, notified, available,"
                " last_notified_cents, min_drop_cents, min_drop_bps FROM watches ORDER BY id"
            ).fetchall()
        return [self._row_to_watch(row) for row in rows]

    def get(self, watch_id: int) -> Watch | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT id, product_id, title, url, target_cents, notified, available,"
                " last_notified_cents, min_drop_cents, min_drop_bps FROM watches WHERE id = ?",
                (watch_id,),
            ).fetchone()
        return self._row_to_watch(row) if row else None

    def update_target(
        self,
        watch_id: int,
        target_cents: int,
        min_drop_cents: int | None = None,
        min_drop_bps: int | None = None,
        update_min_drop: bool = False,
    ) -> Watch | None:
        with self._connection() as db:
            if update_min_drop:
                db.execute(
                    "UPDATE watches SET target_cents = ?, min_drop_cents = ?, min_drop_bps = ?,"
                    " notified = 0, last_notified_cents = NULL WHERE id = ?",
                    (target_cents, min_drop_cents, min_drop_bps, watch_id),
                )
            else:
                db.execute(
                    "UPDATE watches SET target_cents = ?, notified = 0, last_notified_cents = NULL"
                    " WHERE id = ?",
                    (target_cents, watch_id),
                )
        return self.get(watch_id)

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

    def price_stats(self, watch_id: int, since: datetime) -> PriceStats:
        with self._connection() as db:
            latest = db.execute(
                "SELECT price_cents FROM price_points WHERE watch_id = ? ORDER BY checked_at DESC LIMIT 1",
                (watch_id,),
            ).fetchone()
            agg = db.execute(
                "SELECT count(*), min(price_cents), max(price_cents) FROM price_points"
                " WHERE watch_id = ? AND checked_at >= ?",
                (watch_id, since.isoformat()),
            ).fetchone()
        return PriceStats(
            agg[0] if agg else 0,
            latest[0] if latest else None,
            agg[1] if agg else None,
            agg[2] if agg else None,
        )

    def price_history(self, watch_id: int, limit: int = 20) -> list[tuple[str, int]]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT checked_at, price_cents FROM price_points WHERE watch_id = ?"
                " ORDER BY checked_at DESC LIMIT ?",
                (watch_id, limit),
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def prune_price_points(self, before: datetime) -> int:
        with self._connection() as db:
            return db.execute("DELETE FROM price_points WHERE checked_at < ?", (before.isoformat(),)).rowcount

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

    def should_report_error(self, error: str, now: datetime) -> bool:
        with self._connection() as db:
            row = db.execute("SELECT last_error, reported_at FROM check_status WHERE id = 1").fetchone()
        if not row or row[0] != error or not row[1]:
            return True
        reported_at = datetime.fromisoformat(row[1])
        return (now - reported_at) >= timedelta(hours=24)

    def last_error(self) -> str | None:
        with self._connection() as db:
            row = db.execute("SELECT last_error FROM check_status WHERE id = 1").fetchone()
        return row[0] if row else None

    def set_last_error(self, error: str | None, now: datetime | None = None) -> None:
        reported_at = now.isoformat() if now is not None else None
        with self._connection() as db:
            db.execute(
                "INSERT INTO check_status (id, last_error, reported_at) VALUES (1, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET "
                "last_error = excluded.last_error, reported_at = excluded.reported_at",
                (error, reported_at),
            )

    def clear_last_error(self) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM check_status WHERE id = 1")
