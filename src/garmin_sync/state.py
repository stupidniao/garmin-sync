"""Local compare-state persistence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


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
    activity_name: str | None = None
    start_time_local: str | None = None
    error: str | None = None


class JsonlStateStore:
    """Append-only JSONL state store."""

    def __init__(self, state_dir: Path, filename: str = "steps_compare.jsonl") -> None:
        self._path = state_dir / filename

    def append(
        self,
        record: (
            CompareStateRecord
            | SyncStateRecord
            | TrainingScheduleStateRecord
            | ActivitySyncStateRecord
        ),
    ) -> None:
        """Append one compare record."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, sort_keys=True)
            handle.write("\n")

    def records(self) -> list[dict[str, object]]:
        """Read all existing records from the state store."""

        if not self._path.exists():
            return []

        records: list[dict[str, object]] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    records.append(json.loads(stripped))
        return records

    @property
    def path(self) -> Path:
        """Return the backing JSONL path."""

        return self._path


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for state records."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()
