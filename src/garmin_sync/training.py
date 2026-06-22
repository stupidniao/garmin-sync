"""Training schedule sync workflow."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from garmin_sync.dates import month_keys
from garmin_sync.normalize import payload_hash
from garmin_sync.retry import retry_read
from garmin_sync.state import (
    JsonlStateStore,
    TrainingScheduleStateRecord,
    utc_timestamp,
)
from garmin_sync.steps import GLOBAL_TO_CN, validate_direction

STATE_FILENAME = "training_schedule_sync.jsonl"
ACCOUNT_SPECIFIC_KEYS = {
    "author",
    "childStepId",
    "createdDate",
    "ownerId",
    "sharedWithUsers",
    "stepId",
    "updatedDate",
    "uploadTimestamp",
    "userProfileId",
    "userProfilePk",
    "workoutId",
}


@dataclass(frozen=True)
class ScheduledWorkout:
    """One scheduled Garmin workout from a calendar payload."""

    date: str
    scheduled_workout_id: int
    workout_id: int
    title: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class TrainingSyncResult:
    """Training schedule sync result for one scheduled workout."""

    date: str
    status: str
    workout_name: str | None
    workout_hash: str | None
    source_scheduled_workout_id: int | None
    source_workout_id: int | None
    target_scheduled_workout_id: int | None = None
    target_workout_id: int | None = None
    error: str | None = None


def fetch_scheduled_workouts_range(
    client: Any,
    start: date,
    end: date,
) -> list[ScheduledWorkout]:
    """Read scheduled workouts in an inclusive date range."""

    by_schedule_id: dict[int, ScheduledWorkout] = {}
    for year, month in month_keys(start, end):
        payload = retry_read(lambda: client.get_scheduled_workouts(year, month))
        if not isinstance(payload, dict):
            continue
        for item in payload.get("calendarItems", []):
            workout = _scheduled_workout_from_item(item, start, end)
            if workout is not None:
                by_schedule_id[workout.scheduled_workout_id] = workout

    return sorted(
        by_schedule_id.values(),
        key=lambda workout: (workout.date, workout.scheduled_workout_id),
    )


def normalize_workout_for_upload(workout: dict[str, Any]) -> dict[str, Any]:
    """Remove source-account fields from a workout before upload."""

    normalized = _remove_keys(deepcopy(workout), ACCOUNT_SPECIFIC_KEYS)
    if isinstance(normalized, dict):
        normalized["shared"] = False
    return normalized


def workout_name(workout: dict[str, Any], fallback: str | None = None) -> str | None:
    """Return the user-visible workout name."""

    name = workout.get("workoutName") or workout.get("title") or fallback
    return str(name) if name else None


def workout_identity_hash(workout: dict[str, Any]) -> str:
    """Return a stable hash for a normalized workout."""

    return payload_hash(normalize_workout_for_upload(workout))


def sync_training_schedule_range(
    source_client: Any,
    target_client: Any,
    start: date,
    end: date,
    state_dir: Path,
    *,
    direction: str = GLOBAL_TO_CN,
    dry_run: bool = False,
    force: bool = False,
) -> list[TrainingSyncResult]:
    """Sync scheduled workouts from Garmin International to Garmin China."""

    validate_direction(direction)
    state_store = JsonlStateStore(state_dir, STATE_FILENAME)
    existing_records = state_store.records()
    synced_hashes = _synced_hashes(existing_records)
    pending_uploads = _pending_uploaded_workouts(existing_records)
    run_timestamp = utc_timestamp()
    results: list[TrainingSyncResult] = []

    try:
        source_scheduled = fetch_scheduled_workouts_range(source_client, start, end)
        target_scheduled = fetch_scheduled_workouts_range(target_client, start, end)
    except Exception as exc:
        result = TrainingSyncResult(
            date=start.isoformat(),
            status="read_error",
            workout_name=None,
            workout_hash=None,
            source_scheduled_workout_id=None,
            source_workout_id=None,
            error=f"{type(exc).__name__}: {exc}",
        )
        _append_training_record(state_store, run_timestamp, direction, result)
        return [result]

    target_names_by_date = _names_by_date(target_scheduled)
    for scheduled in source_scheduled:
        result = _sync_one_scheduled_workout(
            source_client=source_client,
            target_client=target_client,
            scheduled=scheduled,
            synced_hashes=synced_hashes,
            target_names_by_date=target_names_by_date,
            pending_uploads=pending_uploads,
            state_store=state_store,
            run_timestamp=run_timestamp,
            direction=direction,
            dry_run=dry_run,
            force=force,
        )
        _append_training_record(state_store, run_timestamp, direction, result)
        if result.status == "synced" and result.workout_hash is not None:
            synced_hashes.add((result.date, result.workout_hash))
            if result.workout_name:
                target_names_by_date.setdefault(result.date, set()).add(result.workout_name)
        if result.target_workout_id is not None and result.workout_hash is not None:
            pending_uploads[(result.date, result.workout_hash)] = result.target_workout_id
        results.append(result)

    return results


def _sync_one_scheduled_workout(
    source_client: Any,
    target_client: Any,
    scheduled: ScheduledWorkout,
    synced_hashes: set[tuple[str, str]],
    target_names_by_date: dict[str, set[str]],
    pending_uploads: dict[tuple[str, str], int],
    state_store: JsonlStateStore,
    run_timestamp: str,
    direction: str,
    dry_run: bool,
    force: bool,
) -> TrainingSyncResult:
    try:
        source_workout = retry_read(
            lambda: source_client.get_workout_by_id(scheduled.workout_id)
        )
    except Exception as exc:
        return TrainingSyncResult(
            date=scheduled.date,
            status="read_error",
            workout_name=scheduled.title,
            workout_hash=None,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(source_workout, dict):
        return TrainingSyncResult(
            date=scheduled.date,
            status="read_error",
            workout_name=scheduled.title,
            workout_hash=None,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
            error="Global workout detail was not a mapping.",
        )

    normalized = normalize_workout_for_upload(source_workout)
    name = workout_name(normalized, scheduled.title)
    identity_hash = payload_hash(normalized)

    if not force and (scheduled.date, identity_hash) in synced_hashes:
        return TrainingSyncResult(
            date=scheduled.date,
            status="skipped_state",
            workout_name=name,
            workout_hash=identity_hash,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
        )

    if (
        not force
        and name is not None
        and name in target_names_by_date.get(scheduled.date, set())
    ):
        return TrainingSyncResult(
            date=scheduled.date,
            status="skipped_existing",
            workout_name=name,
            workout_hash=identity_hash,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
            error="CN already has a scheduled workout with the same name on this date.",
        )

    if dry_run:
        return TrainingSyncResult(
            date=scheduled.date,
            status="dry_run",
            workout_name=name,
            workout_hash=identity_hash,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
        )

    target_workout_id = (
        None if force else pending_uploads.get((scheduled.date, identity_hash))
    )
    if target_workout_id is None:
        try:
            upload_result = target_client.upload_workout(normalized)
            target_workout_id = _extract_workout_id(upload_result)
            if target_workout_id is None:
                raise ValueError("CN upload response did not include workoutId")
        except Exception as exc:
            return TrainingSyncResult(
                date=scheduled.date,
                status="upload_error",
                workout_name=name,
                workout_hash=identity_hash,
                source_scheduled_workout_id=scheduled.scheduled_workout_id,
                source_workout_id=scheduled.workout_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        uploaded_result = TrainingSyncResult(
            date=scheduled.date,
            status="uploaded",
            workout_name=name,
            workout_hash=identity_hash,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
            target_workout_id=target_workout_id,
        )
        _append_training_record(state_store, run_timestamp, direction, uploaded_result)

    try:
        schedule_result = target_client.schedule_workout(
            target_workout_id,
            scheduled.date,
        )
        target_scheduled_workout_id = _extract_schedule_id(schedule_result)
    except Exception as exc:
        return TrainingSyncResult(
            date=scheduled.date,
            status="schedule_error",
            workout_name=name,
            workout_hash=identity_hash,
            source_scheduled_workout_id=scheduled.scheduled_workout_id,
            source_workout_id=scheduled.workout_id,
            target_workout_id=target_workout_id,
            error=f"{type(exc).__name__}: {exc}",
        )

    return TrainingSyncResult(
        date=scheduled.date,
        status="synced",
        workout_name=name,
        workout_hash=identity_hash,
        source_scheduled_workout_id=scheduled.scheduled_workout_id,
        source_workout_id=scheduled.workout_id,
        target_scheduled_workout_id=target_scheduled_workout_id,
        target_workout_id=target_workout_id,
    )


def _scheduled_workout_from_item(
    item: Any,
    start: date,
    end: date,
) -> ScheduledWorkout | None:
    if not isinstance(item, dict):
        return None
    if item.get("itemType") != "workout":
        return None
    date_str = item.get("date")
    workout_id = item.get("workoutId")
    scheduled_id = item.get("id")
    if not isinstance(date_str, str) or workout_id is None or scheduled_id is None:
        return None

    try:
        item_date = date.fromisoformat(date_str)
    except ValueError:
        return None
    if item_date < start or item_date > end:
        return None

    title = str(item.get("title") or "")
    return ScheduledWorkout(
        date=date_str,
        scheduled_workout_id=int(scheduled_id),
        workout_id=int(workout_id),
        title=title,
        raw=item,
    )


def _remove_keys(value: Any, keys_to_remove: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_keys(child, keys_to_remove)
            for key, child in value.items()
            if key not in keys_to_remove
        }
    if isinstance(value, list):
        return [_remove_keys(child, keys_to_remove) for child in value]
    return value


def _names_by_date(scheduled_workouts: list[ScheduledWorkout]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for workout in scheduled_workouts:
        if workout.title:
            names.setdefault(workout.date, set()).add(workout.title)
    return names


def _synced_hashes(records: list[dict[str, object]]) -> set[tuple[str, str]]:
    synced: set[tuple[str, str]] = set()
    for record in records:
        if record.get("status") != "synced":
            continue
        date_value = record.get("date")
        hash_value = record.get("workout_hash")
        if isinstance(date_value, str) and isinstance(hash_value, str):
            synced.add((date_value, hash_value))
    return synced


def _pending_uploaded_workouts(
    records: list[dict[str, object]],
) -> dict[tuple[str, str], int]:
    pending: dict[tuple[str, str], int] = {}
    for record in records:
        if record.get("status") not in {"uploaded", "schedule_error"}:
            continue
        date_value = record.get("date")
        hash_value = record.get("workout_hash")
        target_workout_id = record.get("target_workout_id")
        if (
            isinstance(date_value, str)
            and isinstance(hash_value, str)
            and isinstance(target_workout_id, int)
        ):
            pending[(date_value, hash_value)] = target_workout_id
    return pending


def _extract_workout_id(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("workoutId", "id"):
            value = payload.get(key)
            if value is not None:
                return int(value)
        nested = payload.get("workout")
        if nested is not None:
            return _extract_workout_id(nested)
    return None


def _extract_schedule_id(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("workoutScheduleId", "id", "scheduleId"):
            value = payload.get(key)
            if value is not None:
                return int(value)
    return None


def _append_training_record(
    state_store: JsonlStateStore,
    run_timestamp: str,
    direction: str,
    result: TrainingSyncResult,
) -> None:
    state_store.append(
        TrainingScheduleStateRecord(
            run_timestamp=run_timestamp,
            direction=direction,
            date=result.date,
            status=result.status,
            workout_name=result.workout_name,
            workout_hash=result.workout_hash,
            source_scheduled_workout_id=result.source_scheduled_workout_id,
            source_workout_id=result.source_workout_id,
            target_scheduled_workout_id=result.target_scheduled_workout_id,
            target_workout_id=result.target_workout_id,
            error=result.error,
        )
    )
