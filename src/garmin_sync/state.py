"""Local sync-state persistence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CompareStateRecord:
    """One persisted compare result."""

    run_timestamp: str
    direction: str
    metric: str
    date: str
    status: str
    source_hash: str | None
    target_hash: str | None
    error: str | None = None


@dataclass(frozen=True)
class SyncStateRecord:
    """One persisted sync decision."""

    run_timestamp: str
    direction: str
    metric: str
    date: str
    status: str
    source_steps: int | None
    target_steps: int | None
    steps_to_sync: int | None
    error: str | None = None


@dataclass(frozen=True)
class TrainingScheduleStateRecord:
    """One persisted training schedule sync result."""

    run_timestamp: str
    direction: str
    date: str
    status: str
    workout_name: str | None
    workout_hash: str | None
    source_scheduled_workout_id: int | None
    source_workout_id: int | None
    target_scheduled_workout_id: int | None = None
    target_workout_id: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ActivitySyncStateRecord:
    """One persisted activity sync result."""

    run_timestamp: str
    direction: str
    date: str
    status: str
    source_activity_id: int | None
    target_activity_id: int | None = None
    source_workout_id: int | None = None
    target_workout_id: int | None = None
    workout_link_status: str | None = None
    activity_name: str | None = None
    start_time_local: str | None = None
    error: str | None = None


StateRecord = (
    CompareStateRecord
    | SyncStateRecord
    | TrainingScheduleStateRecord
    | ActivitySyncStateRecord
)


class JsonlStateStore:
    """SQLite state store that imports legacy JSONL state once."""

    def __init__(self, state_dir: Path, filename: str = "steps_compare.jsonl") -> None:
        self._state_dir = state_dir
        self._filename = filename
        self._path = state_dir / "state.sqlite3"
        self._table = _table_for_filename(filename)
        self._ensure_ready()

    def append(self, record: StateRecord) -> None:
        """Persist one state record."""

        row = asdict(record)
        with self._connect() as connection:
            if isinstance(record, CompareStateRecord):
                _insert_compare(connection, row)
            elif isinstance(record, SyncStateRecord):
                _insert_sync(connection, row)
            elif isinstance(record, TrainingScheduleStateRecord):
                _upsert_training(connection, row)
            elif isinstance(record, ActivitySyncStateRecord):
                _upsert_activity(connection, row)

    def records(self) -> list[dict[str, object]]:
        """Read all existing records from the selected state table."""

        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {self._table} ORDER BY id"
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    @property
    def path(self) -> Path:
        """Return the backing SQLite path."""

        return self._path

    def _ensure_ready(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            _create_schema(connection)
            _migrate_legacy_jsonl(connection, self._state_dir, self._filename)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for state records."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def read_existing_state_records(state_dir: Path, filename: str) -> list[dict[str, object]]:
    """Read state records without creating or migrating local state."""

    sqlite_path = state_dir / "state.sqlite3"
    if sqlite_path.exists():
        table = _table_for_filename(filename)
        uri = f"file:{sqlite_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if table_exists is not None:
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                records = [_row_to_record(row) for row in rows]
                if records:
                    return records

    records: list[dict[str, object]] = []
    for path in _legacy_jsonl_paths(state_dir, filename):
        if path.exists():
            records.extend(_read_jsonl_records(path))
    return records


def _table_for_filename(filename: str) -> str:
    tables = {
        "steps_compare.jsonl": "steps_compare",
        "steps_sync.jsonl": "steps_sync",
        "training_schedule_sync.jsonl": "training_schedule_sync",
        "activity_sync.jsonl": "activity_sync",
    }
    return tables[filename]


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS state_migrations (
            filename TEXT PRIMARY KEY,
            migrated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS steps_compare (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            direction TEXT,
            metric TEXT,
            date TEXT,
            status TEXT,
            source_hash TEXT,
            target_hash TEXT,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS steps_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            direction TEXT,
            metric TEXT,
            date TEXT,
            status TEXT,
            source_steps INTEGER,
            target_steps INTEGER,
            steps_to_sync INTEGER,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS training_schedule_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            direction TEXT,
            date TEXT,
            status TEXT,
            workout_name TEXT,
            workout_hash TEXT,
            source_scheduled_workout_id INTEGER,
            source_workout_id INTEGER,
            target_scheduled_workout_id INTEGER,
            target_workout_id INTEGER,
            error TEXT,
            UNIQUE(direction, date, workout_hash)
        );

        CREATE TABLE IF NOT EXISTS activity_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT,
            direction TEXT,
            date TEXT,
            status TEXT,
            source_activity_id INTEGER,
            target_activity_id INTEGER,
            source_workout_id INTEGER,
            target_workout_id INTEGER,
            workout_link_status TEXT,
            activity_name TEXT,
            start_time_local TEXT,
            error TEXT,
            UNIQUE(direction, source_activity_id)
        );
        """
    )
    _ensure_columns(
        connection,
        "activity_sync",
        {
            "source_workout_id": "INTEGER",
            "target_workout_id": "INTEGER",
            "workout_link_status": "TEXT",
        },
    )


def _ensure_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, column_type in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def _migrate_legacy_jsonl(
    connection: sqlite3.Connection,
    state_dir: Path,
    filename: str,
) -> None:
    migrated = connection.execute(
        "SELECT 1 FROM state_migrations WHERE filename = ?",
        (filename,),
    ).fetchone()
    if migrated is not None:
        return

    for path in _legacy_jsonl_paths(state_dir, filename):
        if not path.exists():
            continue
        for record in _read_jsonl_records(path):
            _insert_legacy_record(connection, filename, record)

    connection.execute(
        "INSERT INTO state_migrations(filename, migrated_at) VALUES(?, ?)",
        (filename, utc_timestamp()),
    )


def _legacy_jsonl_paths(state_dir: Path, filename: str) -> list[Path]:
    paths = [state_dir / filename]
    if (
        state_dir.name == "default"
        and state_dir.parent.name == "profiles"
        and len(state_dir.parents) >= 2
    ):
        paths.append(state_dir.parents[1] / filename)
    return list(dict.fromkeys(paths))


def _read_jsonl_records(path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _insert_legacy_record(
    connection: sqlite3.Connection,
    filename: str,
    record: dict[str, object],
) -> None:
    if filename == "steps_compare.jsonl":
        _insert_compare(connection, record)
    elif filename == "steps_sync.jsonl":
        _insert_sync(connection, record)
    elif filename == "training_schedule_sync.jsonl":
        _upsert_training(connection, record)
    elif filename == "activity_sync.jsonl":
        _upsert_activity(connection, record)


def _insert_compare(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO steps_compare(
            run_timestamp, direction, metric, date, status, source_hash, target_hash, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("run_timestamp"),
            row.get("direction"),
            row.get("metric"),
            row.get("date"),
            row.get("status"),
            row.get("source_hash"),
            row.get("target_hash"),
            row.get("error"),
        ),
    )


def _insert_sync(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO steps_sync(
            run_timestamp, direction, metric, date, status,
            source_steps, target_steps, steps_to_sync, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("run_timestamp"),
            row.get("direction"),
            row.get("metric"),
            row.get("date"),
            row.get("status"),
            row.get("source_steps"),
            row.get("target_steps"),
            row.get("steps_to_sync"),
            row.get("error"),
        ),
    )


def _upsert_training(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO training_schedule_sync(
            run_timestamp, direction, date, status, workout_name, workout_hash,
            source_scheduled_workout_id, source_workout_id,
            target_scheduled_workout_id, target_workout_id, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(direction, date, workout_hash) DO UPDATE SET
            run_timestamp = excluded.run_timestamp,
            status = excluded.status,
            workout_name = excluded.workout_name,
            source_scheduled_workout_id = excluded.source_scheduled_workout_id,
            source_workout_id = excluded.source_workout_id,
            target_scheduled_workout_id = COALESCE(
                excluded.target_scheduled_workout_id,
                training_schedule_sync.target_scheduled_workout_id
            ),
            target_workout_id = COALESCE(
                excluded.target_workout_id,
                training_schedule_sync.target_workout_id
            ),
            error = excluded.error
        """,
        (
            row.get("run_timestamp"),
            row.get("direction"),
            row.get("date"),
            row.get("status"),
            row.get("workout_name"),
            row.get("workout_hash"),
            row.get("source_scheduled_workout_id"),
            row.get("source_workout_id"),
            row.get("target_scheduled_workout_id"),
            row.get("target_workout_id"),
            row.get("error"),
        ),
    )


def _upsert_activity(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO activity_sync(
            run_timestamp, direction, date, status, source_activity_id,
            target_activity_id, source_workout_id, target_workout_id,
            workout_link_status, activity_name, start_time_local, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(direction, source_activity_id) DO UPDATE SET
            run_timestamp = excluded.run_timestamp,
            date = excluded.date,
            status = excluded.status,
            target_activity_id = COALESCE(
                excluded.target_activity_id,
                activity_sync.target_activity_id
            ),
            source_workout_id = COALESCE(
                excluded.source_workout_id,
                activity_sync.source_workout_id
            ),
            target_workout_id = COALESCE(
                excluded.target_workout_id,
                activity_sync.target_workout_id
            ),
            workout_link_status = excluded.workout_link_status,
            activity_name = excluded.activity_name,
            start_time_local = excluded.start_time_local,
            error = excluded.error
        """,
        (
            row.get("run_timestamp"),
            row.get("direction"),
            row.get("date"),
            row.get("status"),
            row.get("source_activity_id"),
            row.get("target_activity_id"),
            row.get("source_workout_id"),
            row.get("target_workout_id"),
            row.get("workout_link_status"),
            row.get("activity_name"),
            row.get("start_time_local"),
            row.get("error"),
        ),
    )


def _row_to_record(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys() if key != "id"}
