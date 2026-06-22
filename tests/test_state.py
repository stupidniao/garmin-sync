import json
import sqlite3

from garmin_sync.state import JsonlStateStore


def test_sqlite_state_imports_legacy_jsonl_once(tmp_path) -> None:
    legacy_path = tmp_path / "steps_compare.jsonl"
    legacy_path.write_text(
        json.dumps(
            {
                "run_timestamp": "2026-06-11T00:00:00+00:00",
                "direction": "global_to_cn",
                "metric": "steps",
                "date": "2026-06-11",
                "status": "same",
                "source_hash": "a",
                "target_hash": "a",
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    first_store = JsonlStateStore(tmp_path, "steps_compare.jsonl")
    second_store = JsonlStateStore(tmp_path, "steps_compare.jsonl")

    assert len(first_store.records()) == 1
    assert len(second_store.records()) == 1
    assert (tmp_path / "state.sqlite3").exists()
    assert legacy_path.exists()


def test_legacy_training_state_preserves_duplicate_skip(tmp_path) -> None:
    (tmp_path / "training_schedule_sync.jsonl").write_text(
        json.dumps(
            {
                "date": "2026-06-11",
                "direction": "global_to_cn",
                "status": "synced",
                "workout_hash": "abc",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = JsonlStateStore(tmp_path, "training_schedule_sync.jsonl").records()

    assert records[0]["status"] == "synced"
    assert records[0]["workout_hash"] == "abc"


def test_activity_state_adds_workout_columns_to_existing_sqlite(tmp_path) -> None:
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        connection.execute(
            """
            CREATE TABLE activity_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_timestamp TEXT,
                direction TEXT,
                date TEXT,
                status TEXT,
                source_activity_id INTEGER,
                target_activity_id INTEGER,
                activity_name TEXT,
                start_time_local TEXT,
                error TEXT,
                UNIQUE(direction, source_activity_id)
            )
            """
        )

    JsonlStateStore(tmp_path, "activity_sync.jsonl")

    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(activity_sync)")
        }

    assert "source_workout_id" in columns
    assert "target_workout_id" in columns
    assert "workout_link_status" in columns
