import json
from datetime import date

from garmin_sync.training import (
    fetch_scheduled_workouts_range,
    normalize_workout_for_upload,
    sync_training_schedule_range,
    workout_identity_hash,
)


class FakeTrainingClient:
    def __init__(
        self,
        schedules: dict[tuple[int, int], dict[str, object]],
        workouts: dict[int, dict[str, object]] | None = None,
    ) -> None:
        self.schedules = schedules
        self.workouts = workouts or {}
        self.schedule_calls: list[tuple[int, int]] = []
        self.uploaded: list[dict[str, object]] = []
        self.scheduled: list[tuple[int, str]] = []

    def get_scheduled_workouts(self, year: int, month: int) -> dict[str, object]:
        self.schedule_calls.append((year, month))
        return self.schedules.get((year, month), {"calendarItems": []})

    def get_workout_by_id(self, workout_id: int) -> dict[str, object]:
        return self.workouts[workout_id]

    def upload_workout(self, workout: dict[str, object]) -> dict[str, int]:
        self.uploaded.append(workout)
        return {"workoutId": 9000 + len(self.uploaded)}

    def schedule_workout(self, workout_id: int, date_str: str) -> dict[str, int]:
        self.scheduled.append((workout_id, date_str))
        return {"workoutScheduleId": 8000 + len(self.scheduled)}


def _calendar_item(
    *,
    date_str: str,
    schedule_id: int = 100,
    workout_id: int = 200,
    title: str = "Easy Run",
    item_type: str = "workout",
) -> dict[str, object]:
    return {
        "date": date_str,
        "id": schedule_id,
        "itemType": item_type,
        "title": title,
        "workoutId": workout_id,
    }


def _workout(workout_id: int = 200, name: str = "Easy Run") -> dict[str, object]:
    return {
        "workoutId": workout_id,
        "ownerId": 123,
        "author": {"userProfilePk": 123},
        "createdDate": "2026-06-11T10:00:00.0",
        "updatedDate": "2026-06-11T10:00:00.0",
        "shared": True,
        "workoutName": name,
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "workoutSteps": [
                    {
                        "stepId": 999,
                        "childStepId": 888,
                        "stepOrder": 1,
                        "endConditionValue": 8000.0,
                    }
                ],
            }
        ],
    }


def test_fetch_scheduled_workouts_range_reads_each_month_and_filters_items() -> None:
    client = FakeTrainingClient(
        {
            (2026, 6): {
                "calendarItems": [
                    _calendar_item(date_str="2026-06-10", schedule_id=1),
                    _calendar_item(date_str="2026-06-11", schedule_id=2),
                    _calendar_item(date_str="2026-06-12", schedule_id=3, item_type="activity"),
                ]
            },
            (2026, 7): {
                "calendarItems": [
                    _calendar_item(date_str="2026-07-11", schedule_id=4),
                    _calendar_item(date_str="2026-07-12", schedule_id=5),
                ]
            },
        }
    )

    results = fetch_scheduled_workouts_range(
        client,
        date(2026, 6, 11),
        date(2026, 7, 11),
    )

    assert client.schedule_calls == [(2026, 6), (2026, 7)]
    assert [(result.date, result.scheduled_workout_id) for result in results] == [
        ("2026-06-11", 2),
        ("2026-07-11", 4),
    ]


def test_normalize_workout_for_upload_removes_account_specific_fields() -> None:
    normalized = normalize_workout_for_upload(_workout())

    serialized = json.dumps(normalized)
    assert "workoutId" not in serialized
    assert "ownerId" not in serialized
    assert "stepId" not in serialized
    assert normalized["shared"] is False
    assert normalized["workoutName"] == "Easy Run"


def test_sync_training_schedule_dry_run_does_not_write_to_garmin(tmp_path) -> None:
    source = FakeTrainingClient(
        {(2026, 6): {"calendarItems": [_calendar_item(date_str="2026-06-11")]}},
        {200: _workout()},
    )
    target = FakeTrainingClient({(2026, 6): {"calendarItems": []}})

    results = sync_training_schedule_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 11),
        tmp_path,
        dry_run=True,
    )

    assert results[0].status == "dry_run"
    assert target.uploaded == []
    assert target.scheduled == []


def test_sync_training_schedule_uploads_and_schedules_workout(tmp_path) -> None:
    source = FakeTrainingClient(
        {(2026, 6): {"calendarItems": [_calendar_item(date_str="2026-06-11")]}},
        {200: _workout()},
    )
    target = FakeTrainingClient({(2026, 6): {"calendarItems": []}})

    results = sync_training_schedule_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "synced"
    assert results[0].target_workout_id == 9001
    assert results[0].target_scheduled_workout_id == 8001
    assert target.scheduled == [(9001, "2026-06-11")]

    records = [
        json.loads(line)
        for line in (tmp_path / "training_schedule_sync.jsonl").read_text().splitlines()
    ]
    assert records[0]["status"] == "synced"
    assert records[0]["workout_name"] == "Easy Run"
    assert "password" not in json.dumps(records)


def test_sync_training_schedule_skips_same_day_same_name(tmp_path) -> None:
    source = FakeTrainingClient(
        {(2026, 6): {"calendarItems": [_calendar_item(date_str="2026-06-11")]}},
        {200: _workout()},
    )
    target = FakeTrainingClient(
        {(2026, 6): {"calendarItems": [_calendar_item(date_str="2026-06-11")]}}
    )

    results = sync_training_schedule_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "skipped_existing"
    assert target.uploaded == []


def test_sync_training_schedule_skips_previously_synced_hash(tmp_path) -> None:
    source_workout = _workout()
    synced_hash = workout_identity_hash(source_workout)
    (tmp_path / "training_schedule_sync.jsonl").write_text(
        json.dumps(
            {
                "date": "2026-06-11",
                "status": "synced",
                "workout_hash": synced_hash,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = FakeTrainingClient(
        {(2026, 6): {"calendarItems": [_calendar_item(date_str="2026-06-11")]}},
        {200: source_workout},
    )
    target = FakeTrainingClient({(2026, 6): {"calendarItems": []}})

    results = sync_training_schedule_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 11),
        tmp_path,
    )

    assert results[0].status == "skipped_state"
    assert target.uploaded == []
