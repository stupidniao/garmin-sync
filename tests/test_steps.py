import json
from datetime import date

import pytest

from garmin_sync.steps import (
    MetricRead,
    compare_payloads,
    compare_steps_range,
    decide_steps_sync,
    extract_total_steps,
    read_steps,
    sync_steps_range,
    validate_direction,
)
from garmin_sync.state import JsonlStateStore


class FakeClient:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str]] = []

    def get_daily_steps(self, start: str, end: str) -> object:
        self.calls.append((start, end))
        payload = self.payloads[start]
        if isinstance(payload, Exception):
            raise payload
        return payload


def test_validate_direction_accepts_global_to_cn() -> None:
    assert validate_direction("global_to_cn") == "global_to_cn"


def test_validate_direction_rejects_other_modes() -> None:
    with pytest.raises(ValueError, match="Only global_to_cn"):
        validate_direction("bidirectional")


def test_read_steps_calls_garmin_client_with_single_day_range() -> None:
    client = FakeClient({"2026-06-11": [{"steps": 10}]})

    result = read_steps(client, date(2026, 6, 11))

    assert result.payload == [{"steps": 10}]
    assert client.calls == [("2026-06-11", "2026-06-11")]


def test_compare_payloads_same() -> None:
    result = compare_payloads(
        MetricRead(payload=[{"steps": 10}]),
        MetricRead(payload=[{"steps": 10}]),
        date(2026, 6, 11),
    )

    assert result.status == "same"
    assert result.source_hash == result.target_hash


def test_compare_payloads_different() -> None:
    result = compare_payloads(
        MetricRead(payload=[{"steps": 10}]),
        MetricRead(payload=[{"steps": 20}]),
        date(2026, 6, 11),
    )

    assert result.status == "different"
    assert result.source_hash != result.target_hash


def test_compare_payloads_missing_source() -> None:
    result = compare_payloads(
        MetricRead(payload=[]),
        MetricRead(payload=[{"steps": 20}]),
        date(2026, 6, 11),
    )

    assert result.status == "missing_source"


def test_compare_payloads_missing_target() -> None:
    result = compare_payloads(
        MetricRead(payload=[{"steps": 20}]),
        MetricRead(payload=[]),
        date(2026, 6, 11),
    )

    assert result.status == "missing_target"


def test_compare_payloads_read_error() -> None:
    result = compare_payloads(
        MetricRead(error="source failed"),
        MetricRead(payload=[]),
        date(2026, 6, 11),
    )

    assert result.status == "read_error"
    assert result.error == "source failed"


def test_compare_steps_range_persists_state_without_raw_payloads(tmp_path) -> None:
    source = FakeClient(
        {
            "2026-06-11": [{"steps": 10}],
            "2026-06-12": [{"steps": 20}],
        }
    )
    target = FakeClient(
        {
            "2026-06-11": [{"steps": 10}],
            "2026-06-12": [],
        }
    )

    results = compare_steps_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 12),
        tmp_path,
    )

    assert [result.status for result in results] == ["same", "missing_target"]
    records = JsonlStateStore(tmp_path, "steps_compare.jsonl").records()
    assert len(records) == 2
    assert records[0]["metric"] == "steps"
    assert "password" not in json.dumps(records)
    assert "payload" not in json.dumps(records)


def test_compare_steps_range_dry_run_does_not_persist_state(tmp_path) -> None:
    source = FakeClient({"2026-06-11": [{"steps": 10}]})
    target = FakeClient({"2026-06-11": [{"steps": 10}]})

    results = compare_steps_range(
        source,
        target,
        date(2026, 6, 11),
        date(2026, 6, 11),
        tmp_path,
        persist=False,
    )

    assert results[0].status == "same"
    assert not (tmp_path / "state.sqlite3").exists()


def test_extract_total_steps_from_daily_steps_payload() -> None:
    assert extract_total_steps([{"calendarDate": "2026-06-11", "totalSteps": 10}]) == 10
    assert extract_total_steps([{"calendarDate": "2026-06-11", "totalSteps": None}]) is None
    assert extract_total_steps([]) is None


def test_decide_steps_sync_when_cn_is_lower() -> None:
    result = decide_steps_sync(
        MetricRead(payload=[{"totalSteps": 16710}]),
        MetricRead(payload=[{"totalSteps": None}]),
        date(2026, 6, 10),
    )

    assert result.status == "sync_unavailable"
    assert result.source_steps == 16710
    assert result.target_steps is None
    assert result.steps_to_sync == 16710
    assert "no verified write endpoint" in result.error


def test_decide_steps_sync_disabled_when_cn_is_not_lower() -> None:
    result = decide_steps_sync(
        MetricRead(payload=[{"totalSteps": 100}]),
        MetricRead(payload=[{"totalSteps": 100}]),
        date(2026, 6, 10),
    )

    assert result.status == "sync_disabled"
    assert result.steps_to_sync == 0


def test_sync_steps_range_persists_sync_decision(tmp_path) -> None:
    source = FakeClient({"2026-06-10": [{"totalSteps": 16710}]})
    target = FakeClient({"2026-06-10": [{"totalSteps": None}]})

    results = sync_steps_range(
        source,
        target,
        date(2026, 6, 10),
        date(2026, 6, 10),
        tmp_path,
    )

    assert results[0].status == "sync_unavailable"
    records = JsonlStateStore(tmp_path, "steps_sync.jsonl").records()
    assert records[0]["source_steps"] == 16710
    assert records[0]["target_steps"] is None
    assert records[0]["steps_to_sync"] == 16710


def test_sync_steps_range_dry_run_does_not_persist_state(tmp_path) -> None:
    source = FakeClient({"2026-06-10": [{"totalSteps": 16710}]})
    target = FakeClient({"2026-06-10": [{"totalSteps": None}]})

    results = sync_steps_range(
        source,
        target,
        date(2026, 6, 10),
        date(2026, 6, 10),
        tmp_path,
        persist=False,
    )

    assert results[0].status == "sync_unavailable"
    assert not (tmp_path / "state.sqlite3").exists()
