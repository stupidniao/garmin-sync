import json

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
