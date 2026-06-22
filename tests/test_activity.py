import io
import json
import zipfile
from datetime import date
from enum import Enum, auto

from garmin_sync.activity import (
    extract_fit_from_original_download,
    sync_activities_for_date,
    validate_activity_direction,
)
from garmin_sync.state import JsonlStateStore


class FakeDownloadFormat(Enum):
    ORIGINAL = auto()


class FakeActivityClient:
    ActivityDownloadFormat = FakeDownloadFormat

    def __init__(
        self,
        activities_by_date: dict[str, list[dict[str, object]]],
        original_bytes: bytes | None = None,
        upload_response: dict[str, object] | None = None,
        uploaded_activity: dict[str, object] | None = None,
        activity_details: dict[int, dict[str, object]] | None = None,
    ) -> None:
        self.activities_by_date = activities_by_date
        self.original_bytes = original_bytes or _zip_with_fit(b"FIT bytes")
        self.upload_response = upload_response
        self.uploaded_activity = uploaded_activity
        self.activity_details = activity_details or {}
        self.activity_calls: list[tuple[str, str, str | None]] = []
        self.downloaded: list[tuple[int, FakeDownloadFormat]] = []
        self.uploaded: list[str] = []
        self.workout_links: list[tuple[int, int]] = []

    def get_activities_by_date(
        self,
        startdate: str,
        enddate: str | None = None,
        activitytype: str | None = None,
        sortorder: str | None = None,
    ) -> list[dict[str, object]]:
        self.activity_calls.append((startdate, enddate or "", sortorder))
        return self.activities_by_date.get(startdate, [])

    def download_activity(
        self,
        activity_id: int,
        dl_fmt: FakeDownloadFormat,
    ) -> bytes:
        self.downloaded.append((activity_id, dl_fmt))
        return self.original_bytes

    def upload_activity(self, activity_path: str) -> dict[str, object]:
        self.uploaded.append(activity_path)
        if self.uploaded_activity is not None:
            self.activities_by_date.setdefault("2026-06-11", []).append(
                self.uploaded_activity
            )
        if self.upload_response is not None:
            return self.upload_response
        self.activity_details.setdefault(
            9001,
            {
                "activityId": 9001,
                "metadataDTO": {"associatedWorkoutId": None},
            },
        )
        return {"detailedImportResult": {"successes": [{"internalId": 9001}]}}

    def get_activity(self, activity_id: int) -> dict[str, object]:
        return self.activity_details.get(
            activity_id,
            {
                "activityId": activity_id,
                "metadataDTO": {"associatedWorkoutId": None},
            },
        )

    def set_activity_associated_workout(
        self,
        activity_id: int,
        workout_id: int,
    ) -> None:
        self.workout_links.append((activity_id, workout_id))
        self.activity_details[activity_id] = {
            "activityId": activity_id,
            "metadataDTO": {"associatedWorkoutId": workout_id},
        }


def _activity(
    activity_id: int = 100,
    name: str = "Morning Run",
    start: str = "2026-06-11 08:00:00",
    workout_id: int | None = None,
) -> dict[str, object]:
    activity = {
        "activityId": activity_id,
        "activityName": name,
        "startTimeLocal": start,
    }
    if workout_id is not None:
        activity["workoutId"] = workout_id
    return activity


def _zip_with_fit(content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activity.fit", content)
    return buffer.getvalue()


def test_validate_activity_direction_accepts_cn_to_global() -> None:
    assert validate_activity_direction("cn_to_global") == "cn_to_global"


def test_extract_fit_from_original_download(tmp_path) -> None:
    fit_path = extract_fit_from_original_download(100, _zip_with_fit(b"abc"), tmp_path)

    assert fit_path.name == "activity.fit"
    assert fit_path.read_bytes() == b"abc"


def test_sync_activities_dry_run_does_not_download_or_upload(tmp_path) -> None:
    source = FakeActivityClient({"2026-06-11": [_activity()]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
        dry_run=True,
    )

    assert results[0].status == "dry_run"
    assert source.downloaded == []
    assert target.uploaded == []
    assert not (tmp_path / "state.sqlite3").exists()


def test_sync_activities_reports_no_source_activity(tmp_path) -> None:
    source = FakeActivityClient({"2026-06-11": []})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "no_source_activity"
    records = JsonlStateStore(tmp_path, "activity_sync.jsonl").records()
    assert records[0]["status"] == "no_source_activity"


def test_sync_activities_uploads_original_fit(tmp_path) -> None:
    source = FakeActivityClient({"2026-06-11": [_activity()]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "synced"
    assert results[0].target_activity_id == 9001
    assert source.downloaded == [(100, FakeDownloadFormat.ORIGINAL)]
    assert len(target.uploaded) == 1

    records = JsonlStateStore(tmp_path, "activity_sync.jsonl").records()
    assert records[0]["status"] == "synced"
    assert records[0]["source_activity_id"] == 100


def test_sync_activities_links_target_to_global_workout_from_training_history(
    tmp_path,
) -> None:
    (tmp_path / "training_schedule_sync.jsonl").write_text(
        json.dumps(
            {
                "status": "synced",
                "source_workout_id": 200,
                "target_workout_id": 9000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = FakeActivityClient({"2026-06-11": [_activity(workout_id=9000)]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "synced"
    assert results[0].source_workout_id == 9000
    assert results[0].target_workout_id == 200
    assert results[0].workout_link_status == "linked"
    assert target.workout_links == [(9001, 200)]

    records = JsonlStateStore(tmp_path, "activity_sync.jsonl").records()
    assert records[0]["source_workout_id"] == 9000
    assert records[0]["target_workout_id"] == 200
    assert records[0]["workout_link_status"] == "linked"


def test_sync_activities_dry_run_maps_workout_without_local_state_write(
    tmp_path,
) -> None:
    (tmp_path / "training_schedule_sync.jsonl").write_text(
        json.dumps(
            {
                "status": "synced",
                "source_workout_id": 200,
                "target_workout_id": 9000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = FakeActivityClient({"2026-06-11": [_activity(workout_id=9000)]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
        dry_run=True,
    )

    assert results[0].status == "dry_run"
    assert results[0].source_workout_id == 9000
    assert results[0].target_workout_id == 200
    assert results[0].workout_link_status == "dry_run"
    assert target.workout_links == []
    assert not (tmp_path / "state.sqlite3").exists()


def test_sync_activities_reports_missing_training_workout_mapping(tmp_path) -> None:
    source = FakeActivityClient({"2026-06-11": [_activity(workout_id=9000)]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "workout_link_missing"
    assert results[0].source_workout_id == 9000
    assert results[0].target_workout_id is None
    assert results[0].workout_link_status == "missing_training_mapping"
    assert target.workout_links == []


def test_sync_activities_links_existing_target_activity_from_training_history(
    tmp_path,
) -> None:
    (tmp_path / "training_schedule_sync.jsonl").write_text(
        json.dumps(
            {
                "status": "synced",
                "source_workout_id": 200,
                "target_workout_id": 9000,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    activity = _activity(workout_id=9000)
    source = FakeActivityClient({"2026-06-11": [activity]})
    target = FakeActivityClient(
        {"2026-06-11": [_activity(activity_id=9001)]},
        activity_details={
            9001: {
                "activityId": 9001,
                "metadataDTO": {"associatedWorkoutId": None},
            }
        },
    )

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "skipped_existing"
    assert results[0].workout_link_status == "linked"
    assert results[0].target_activity_id == 9001
    assert target.workout_links == [(9001, 200)]
    assert target.uploaded == []


def test_sync_activities_verifies_upload_without_response_id(tmp_path) -> None:
    activity = _activity(activity_id=9002)
    source = FakeActivityClient({"2026-06-11": [_activity()]})
    target = FakeActivityClient(
        {"2026-06-11": []},
        upload_response={"detailedImportResult": {"successes": []}},
        uploaded_activity=activity,
    )

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "synced"
    assert results[0].target_activity_id == 9002


def test_sync_activities_rejects_unverified_upload(tmp_path) -> None:
    source = FakeActivityClient({"2026-06-11": [_activity()]})
    target = FakeActivityClient(
        {"2026-06-11": []},
        upload_response={"detailedImportResult": {"successes": []}},
    )

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "sync_error"
    assert results[0].target_activity_id is None


def test_sync_activities_skips_existing_target_activity(tmp_path) -> None:
    activity = _activity()
    source = FakeActivityClient({"2026-06-11": [activity]})
    target = FakeActivityClient({"2026-06-11": [activity]})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "skipped_existing"
    assert source.downloaded == []
    assert target.uploaded == []


def test_sync_activities_skips_previously_synced_source_id(tmp_path) -> None:
    (tmp_path / "activity_sync.jsonl").write_text(
        json.dumps(
            {
                "status": "synced",
                "source_activity_id": 100,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = FakeActivityClient({"2026-06-11": [_activity()]})
    target = FakeActivityClient({"2026-06-11": []})

    results = sync_activities_for_date(
        source,
        target,
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "skipped_state"
    assert source.downloaded == []
