"""Activity sync workflow."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from garmin_sync.retry import retry_read
from garmin_sync.state import ActivitySyncStateRecord, JsonlStateStore, utc_timestamp

CN_TO_GLOBAL = "cn_to_global"
STATE_FILENAME = "activity_sync.jsonl"


@dataclass(frozen=True)
class ActivitySyncResult:
    """Activity sync result for one source activity."""

    date: str
    status: str
    source_activity_id: int | None
    target_activity_id: int | None = None
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

    target_keys = {
        key
        for activity in target_activities
        if (key := _activity_dedupe_key(activity)) is not None
    }
    synced_source_ids = _synced_source_ids(store) if store is not None else set()
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
            target_keys=target_keys,
            synced_source_ids=synced_source_ids,
            dry_run=dry_run,
            force=force,
        )
        if store is not None:
            _append_activity_record(store, run_timestamp, direction, result)
        if result.status == "synced" and result.source_activity_id is not None:
            synced_source_ids.add(result.source_activity_id)
            key = (result.start_time_local, result.activity_name)
            if key[0] is not None and key[1] is not None:
                target_keys.add(key)
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
    target_keys: set[tuple[str, str]],
    synced_source_ids: set[int],
    dry_run: bool,
    force: bool,
) -> ActivitySyncResult:
    activity_id = _activity_id(activity)
    activity_name = _activity_name(activity)
    start_time_local = _start_time_local(activity)

    if activity_id is None:
        return ActivitySyncResult(
            date=date_str,
            status="read_error",
            source_activity_id=None,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error="Source activity did not include activityId.",
        )

    if not force and activity_id in synced_source_ids:
        return ActivitySyncResult(
            date=date_str,
            status="skipped_state",
            source_activity_id=activity_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
        )

    dedupe_key = _activity_dedupe_key(activity)
    if not force and dedupe_key is not None and dedupe_key in target_keys:
        return ActivitySyncResult(
            date=date_str,
            status="skipped_existing",
            source_activity_id=activity_id,
            activity_name=activity_name,
            start_time_local=start_time_local,
            error="Target already has an activity with the same start time and name.",
        )

    if dry_run:
        return ActivitySyncResult(
            date=date_str,
            status="dry_run",
            source_activity_id=activity_id,
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
            activity_name=activity_name,
            start_time_local=start_time_local,
            error=f"{type(exc).__name__}: {exc}",
        )

    return ActivitySyncResult(
        date=date_str,
        status="synced",
        source_activity_id=activity_id,
        target_activity_id=target_activity_id,
        activity_name=activity_name,
        start_time_local=start_time_local,
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
            activity_name=result.activity_name,
            start_time_local=result.start_time_local,
            error=result.error,
        )
    )
