"""Activity sync workflow."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from garmin_sync.retry import retry_read
from garmin_sync.state import (
    ActivitySyncStateRecord,
    JsonlStateStore,
    read_existing_state_records,
    utc_timestamp,
)
from garmin_sync.training import STATE_FILENAME as TRAINING_STATE_FILENAME

CN_TO_GLOBAL = "cn_to_global"
STATE_FILENAME = "activity_sync.jsonl"


@dataclass(frozen=True)
class ActivitySyncResult:
    """Activity sync result for one source activity."""

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


def validate_activity_direction(direction: str) -> str:
    """Validate the supported activity sync direction."""

    if direction != CN_TO_GLOBAL:
        raise ValueError("Only cn_to_global is supported for activity sync v1")
    return direction


def sync_activities_for_date(
    source_client: Any,
    target_client: Any,
    sync_date: date,
    state_dir: Path,
    *,
    direction: str = CN_TO_GLOBAL,
    dry_run: bool = False,
    force: bool = False,
    persist: bool = True,
) -> list[ActivitySyncResult]:
    """Sync all source activities for one date into the target account."""

    validate_activity_direction(direction)
    persist_state = persist and not dry_run
    store = JsonlStateStore(state_dir, STATE_FILENAME) if persist_state else None
    run_timestamp = utc_timestamp()
    date_str = sync_date.isoformat()

    try:
        source_activities = _activities_for_date(source_client, date_str)
        target_activities = _activities_for_date(target_client, date_str)
    except Exception as exc:
        result = ActivitySyncResult(
            date=date_str,
            status="read_error",
            source_activity_id=None,
            error=f"{type(exc).__name__}: {exc}",
        )
        if store is not None:
            _append_activity_record(store, run_timestamp, direction, result)
        return [result]

    target_by_key = {
        key: activity
        for activity in target_activities
        if (key := _activity_dedupe_key(activity)) is not None
    }
    synced_source_ids = _synced_source_ids(store) if store is not None else set()
    training_workout_ids = _training_workout_id_map(state_dir)
    results: list[ActivitySyncResult] = []

    if not source_activities:
        result = ActivitySyncResult(
            date=date_str,
            status="no_source_activity",
            source_activity_id=None,
        )
        if store is not None:
            _append_activity_record(store, run_timestamp, direction, result)
        return [result]

    for activity in source_activities:
        result = _sync_one_activity(
            source_client=source_client,
            target_client=target_client,
            activity=activity,
            date_str=date_str,
            target_by_key=target_by_key,
            synced_source_ids=synced_source_ids,
            training_workout_ids=training_workout_ids,
            dry_run=dry_run,
            force=force,
        )
        if store is not None:
            _append_activity_record(store, run_timestamp, direction, result)
        if result.status == "synced" and result.source_activity_id is not None:
            synced_source_ids.add(result.source_activity_id)
            key = (result.start_time_local, result.activity_name)
            if key[0] is not None and key[1] is not None:
                target_by_key[key] = {
                    "activityId": result.target_activity_id,
                    "activityName": result.activity_name,
                    "startTimeLocal": result.start_time_local,
                }
        results.append(result)

    return results


def extract_fit_from_original_download(
    activity_id: int,
    original_bytes: bytes,
    output_dir: Path,
) -> Path:
    """Extract the first FIT file from Garmin's original activity ZIP bytes."""

    zip_path = output_dir / f"{activity_id}.zip"
    zip_path.write_bytes(original_bytes)

    with zipfile.ZipFile(zip_path) as archive:
        fit_names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and name.lower().endswith(".fit")
        ]
        if not fit_names:
            raise ValueError("Original activity download did not contain a FIT file")
        fit_name = fit_names[0]
        fit_path = output_dir / Path(fit_name).name
        fit_path.write_bytes(archive.read(fit_name))
        return fit_path


def _sync_one_activity(
    source_client: Any,
    target_client: Any,
    activity: dict[str, Any],
    date_str: str,
    target_by_key: dict[tuple[str, str], dict[str, Any]],
    synced_source_ids: set[int],
    training_workout_ids: dict[int, int],
    dry_run: bool,
    force: bool,
) -> ActivitySyncResult:
    activity_id = _activity_id(activity)
    activity_name = _activity_name(activity)
    start_time_local = _start_time_local(activity)
    try:
        source_workout_id = _source_activity_workout_id(
            source_client,
            activity_id,
            activity,
        )
    except Exception as exc:
        return ActivitySyncResult(
            date=date_str,
            status="read_error",
            source_activity_id=activity_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=f"{type(exc).__name__}: {exc}",
        )
    target_workout_id = _mapped_global_workout_id(
        training_workout_ids,
        source_workout_id,
    )

    if activity_id is None:
        return ActivitySyncResult(
            date=date_str,
            status="read_error",
            source_activity_id=None,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error="Source activity did not include activityId.",
        )

    if not force and activity_id in synced_source_ids:
        return ActivitySyncResult(
            date=date_str,
            status="skipped_state",
            source_activity_id=activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
        )

    dedupe_key = _activity_dedupe_key(activity)
    if not force and dedupe_key is not None and dedupe_key in target_by_key:
        target_activity_id = _activity_id(target_by_key[dedupe_key])
        return _finish_activity_workout_link(
            target_client=target_client,
            date_str=date_str,
            status_without_link="skipped_existing",
            source_activity_id=activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            dry_run=dry_run,
            error_without_link=(
                "Target already has an activity with the same start time and name."
            ),
        )

    if dry_run:
        return ActivitySyncResult(
            date=date_str,
            status="dry_run",
            source_activity_id=activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="dry_run" if target_workout_id is not None else None,
            activity_name=activity_name,
            start_time_local=start_time_local,
        )

    try:
        with TemporaryDirectory(prefix="garmin-sync-activity-") as tmpdir:
            download_format = source_client.ActivityDownloadFormat.ORIGINAL
            original_bytes = source_client.download_activity(activity_id, download_format)
            fit_path = extract_fit_from_original_download(
                activity_id,
                original_bytes,
                Path(tmpdir),
            )
            upload_result = target_client.upload_activity(str(fit_path))
    except Exception as exc:
        return ActivitySyncResult(
            date=date_str,
            status="sync_error",
            source_activity_id=activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        target_activity_id = _target_activity_id(upload_result)
        if target_activity_id is None:
            target_activity_id = _verify_uploaded_activity(
                target_client,
                date_str,
                activity_name,
                start_time_local,
            )
        if target_activity_id is None:
            raise ValueError("Target upload response did not include a verified activity id")
    except Exception as exc:
        return ActivitySyncResult(
            date=date_str,
            status="sync_error",
            source_activity_id=activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=f"{type(exc).__name__}: {exc}",
        )

    return _finish_activity_workout_link(
        target_client=target_client,
        date_str=date_str,
        status_without_link="synced",
        source_activity_id=activity_id,
        target_activity_id=target_activity_id,
        source_workout_id=source_workout_id,
        target_workout_id=target_workout_id,
        activity_name=activity_name,
        start_time_local=start_time_local,
        dry_run=False,
    )


def _activities_for_date(client: Any, date_str: str) -> list[dict[str, Any]]:
    payload = retry_read(
        lambda: client.get_activities_by_date(date_str, date_str, sortorder="asc")
    )
    if not isinstance(payload, list):
        raise ValueError("Activities payload was not a list")
    return [activity for activity in payload if isinstance(activity, dict)]


def _activity_dedupe_key(activity: dict[str, Any]) -> tuple[str, str] | None:
    start_time_local = _start_time_local(activity)
    activity_name = _activity_name(activity)
    if start_time_local is None or activity_name is None:
        return None
    return (start_time_local, activity_name)


def _activity_id(activity: dict[str, Any]) -> int | None:
    value = activity.get("activityId")
    return _int_or_none(value)


def _target_activity_id(upload_result: Any) -> int | None:
    if not isinstance(upload_result, dict):
        return None
    candidates = [
        upload_result.get("activityId"),
        upload_result.get("internalId"),
    ]
    detailed = upload_result.get("detailedImportResult")
    if isinstance(detailed, dict):
        candidates.append(detailed.get("uploadId"))
        successes = detailed.get("successes")
        if isinstance(successes, list) and successes and isinstance(successes[0], dict):
            candidates.append(successes[0].get("internalId"))
    for value in candidates:
        if value is not None:
            parsed = _int_or_none(value)
            if parsed is not None:
                return parsed
    return None


def _verify_uploaded_activity(
    target_client: Any,
    date_str: str,
    activity_name: str | None,
    start_time_local: str | None,
) -> int | None:
    if activity_name is None or start_time_local is None:
        return None
    for activity in _activities_for_date(target_client, date_str):
        if (
            _activity_name(activity) == activity_name
            and _start_time_local(activity) == start_time_local
        ):
            return _activity_id(activity)
    return None


def _finish_activity_workout_link(
    target_client: Any,
    date_str: str,
    status_without_link: str,
    source_activity_id: int,
    target_activity_id: int | None,
    source_workout_id: int | None,
    target_workout_id: int | None,
    activity_name: str | None,
    start_time_local: str | None,
    dry_run: bool,
    error_without_link: str | None = None,
) -> ActivitySyncResult:
    if source_workout_id is None:
        return ActivitySyncResult(
            date=date_str,
            status=status_without_link,
            source_activity_id=source_activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="no_source_workout",
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=error_without_link,
        )

    if target_workout_id is None:
        return ActivitySyncResult(
            date=date_str,
            status="workout_link_missing",
            source_activity_id=source_activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="missing_training_mapping",
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=(
                f"No training sync history maps CN workout {source_workout_id} "
                "to a Global workout."
            ),
        )

    if target_activity_id is None:
        return ActivitySyncResult(
            date=date_str,
            status="workout_link_missing",
            source_activity_id=source_activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="missing_target_activity",
            activity_name=activity_name,
            start_time_local=start_time_local,
            error="Cannot update target workout id without a target activity id.",
        )

    if dry_run:
        return ActivitySyncResult(
            date=date_str,
            status=status_without_link,
            source_activity_id=source_activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="dry_run",
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=error_without_link,
        )

    try:
        current_workout_id = _target_activity_workout_id(target_client, target_activity_id)
        if current_workout_id != target_workout_id:
            _set_activity_associated_workout(
                target_client,
                target_activity_id,
                target_workout_id,
            )
            current_workout_id = _target_activity_workout_id(
                target_client,
                target_activity_id,
            )
        if current_workout_id != target_workout_id:
            raise ValueError(
                "Target activity did not report the expected Global workout id "
                f"{target_workout_id}; found {current_workout_id}."
            )
    except Exception as exc:
        return ActivitySyncResult(
            date=date_str,
            status="workout_link_error",
            source_activity_id=source_activity_id,
            target_activity_id=target_activity_id,
            source_workout_id=source_workout_id,
            target_workout_id=target_workout_id,
            workout_link_status="error",
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ActivitySyncResult(
        date=date_str,
        status=status_without_link,
        source_activity_id=source_activity_id,
        target_activity_id=target_activity_id,
        source_workout_id=source_workout_id,
        target_workout_id=target_workout_id,
        workout_link_status="linked",
        activity_name=activity_name,
        start_time_local=start_time_local,
        error=error_without_link,
    )


def _source_activity_workout_id(
    source_client: Any,
    activity_id: int | None,
    activity: dict[str, Any],
) -> int | None:
    workout_id = _activity_workout_id(activity)
    if workout_id is not None or activity_id is None:
        return workout_id

    get_activity = getattr(source_client, "get_activity", None)
    if not callable(get_activity):
        return None
    payload = retry_read(lambda: get_activity(activity_id))
    if isinstance(payload, dict):
        return _activity_workout_id(payload)
    return None


def _target_activity_workout_id(target_client: Any, activity_id: int) -> int | None:
    payload = retry_read(lambda: target_client.get_activity(activity_id))
    if isinstance(payload, dict):
        return _activity_workout_id(payload)
    return None


def _activity_workout_id(activity: dict[str, Any]) -> int | None:
    for key in ("workoutId", "associatedWorkoutId"):
        value = _int_or_none(activity.get(key))
        if value is not None:
            return value
    metadata = activity.get("metadataDTO")
    if isinstance(metadata, dict):
        return _int_or_none(metadata.get("associatedWorkoutId"))
    return None


def _training_workout_id_map(state_dir: Path) -> dict[int, int]:
    """Return CN workout id -> Global workout id from training sync history."""

    workout_ids: dict[int, int] = {}
    for record in read_existing_state_records(state_dir, TRAINING_STATE_FILENAME):
        cn_workout_id = _int_or_none(record.get("target_workout_id"))
        global_workout_id = _int_or_none(record.get("source_workout_id"))
        if cn_workout_id is not None and global_workout_id is not None:
            workout_ids[cn_workout_id] = global_workout_id
    return workout_ids


def _mapped_global_workout_id(
    training_workout_ids: dict[int, int],
    source_workout_id: int | None,
) -> int | None:
    if source_workout_id is None:
        return None
    return training_workout_ids.get(source_workout_id)


def _set_activity_associated_workout(
    target_client: Any,
    activity_id: int,
    workout_id: int,
) -> None:
    set_associated_workout = getattr(
        target_client,
        "set_activity_associated_workout",
        None,
    )
    if callable(set_associated_workout):
        set_associated_workout(activity_id, workout_id)
        return

    api_client = getattr(target_client, "client", None)
    activity_url = getattr(target_client, "garmin_connect_activity", None)
    put = getattr(api_client, "put", None)
    if not callable(put) or not isinstance(activity_url, str):
        raise NotImplementedError(
            "Garmin client does not expose activity workout update support."
        )

    payload = {
        "activityId": activity_id,
        "metadataDTO": {"associatedWorkoutId": workout_id},
    }
    put("connectapi", f"{activity_url}/{activity_id}", json=payload, api=True)


def _activity_name(activity: dict[str, Any]) -> str | None:
    value = activity.get("activityName")
    return str(value) if value else None


def _start_time_local(activity: dict[str, Any]) -> str | None:
    value = activity.get("startTimeLocal")
    return str(value) if value else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _synced_source_ids(store: JsonlStateStore) -> set[int]:
    synced: set[int] = set()
    for record in store.records():
        if record.get("status") != "synced":
            continue
        source_activity_id = record.get("source_activity_id")
        if isinstance(source_activity_id, int):
            synced.add(source_activity_id)
    return synced


def _append_activity_record(
    store: JsonlStateStore,
    run_timestamp: str,
    direction: str,
    result: ActivitySyncResult,
) -> None:
    store.append(
        ActivitySyncStateRecord(
            run_timestamp=run_timestamp,
            direction=direction,
            date=result.date,
            status=result.status,
            source_activity_id=result.source_activity_id,
            target_activity_id=result.target_activity_id,
            source_workout_id=result.source_workout_id,
            target_workout_id=result.target_workout_id,
            workout_link_status=result.workout_link_status,
            activity_name=result.activity_name,
            start_time_local=result.start_time_local,
            error=result.error,
        )
    )
