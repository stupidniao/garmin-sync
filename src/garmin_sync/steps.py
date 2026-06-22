"""Steps compare workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from garmin_sync.dates import inclusive_dates
from garmin_sync.normalize import is_missing_payload, payload_hash
from garmin_sync.retry import retry_read
from garmin_sync.state import (
    CompareStateRecord,
    JsonlStateStore,
    SyncStateRecord,
    utc_timestamp,
)

METRIC_NAME = "steps"
GLOBAL_TO_CN = "global_to_cn"


@dataclass(frozen=True)
class MetricRead:
    """Result of reading one metric from one account."""

    payload: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class CompareResult:
    """Steps compare result for one date."""

    date: str
    status: str
    source_hash: str | None
    target_hash: str | None
    error: str | None = None


@dataclass(frozen=True)
class SyncResult:
    """Steps sync decision for one date."""

    date: str
    status: str
    source_steps: int | None
    target_steps: int | None
    steps_to_sync: int | None = None
    error: str | None = None


def validate_direction(direction: str) -> str:
    """Validate the supported steps sync direction."""

    if direction != GLOBAL_TO_CN:
        raise ValueError("Only global_to_cn is supported for steps compare v1")
    return direction


def read_steps(client: Any, day: date) -> MetricRead:
    """Read steps payload from a Garmin client."""

    try:
        date_str = day.isoformat()
        return MetricRead(
            payload=retry_read(lambda: client.get_daily_steps(date_str, date_str))
        )
    except Exception as exc:  # Garmin exceptions vary by installed package version.
        return MetricRead(error=f"{type(exc).__name__}: {exc}")


def compare_payloads(source: MetricRead, target: MetricRead, day: date) -> CompareResult:
    """Compare one source and target steps payload."""

    date_str = day.isoformat()
    if not source.ok or not target.ok:
        errors = [error for error in (source.error, target.error) if error]
        return CompareResult(
            date=date_str,
            status="read_error",
            source_hash=None,
            target_hash=None,
            error="; ".join(errors),
        )

    source_missing = is_missing_payload(source.payload)
    target_missing = is_missing_payload(target.payload)
    source_hash = payload_hash(source.payload)
    target_hash = payload_hash(target.payload)

    if source_missing and not target_missing:
        status = "missing_source"
    elif target_missing and not source_missing:
        status = "missing_target"
    elif source_hash == target_hash:
        status = "same"
    else:
        status = "different"

    return CompareResult(
        date=date_str,
        status=status,
        source_hash=source_hash,
        target_hash=target_hash,
    )


def compare_steps_range(
    source_client: Any,
    target_client: Any,
    start: date,
    end: date,
    state_dir: Path,
    direction: str = GLOBAL_TO_CN,
) -> list[CompareResult]:
    """Compare steps payloads for each day in an inclusive range."""

    validate_direction(direction)
    store = JsonlStateStore(state_dir)
    run_timestamp = utc_timestamp()
    results: list[CompareResult] = []

    for day in inclusive_dates(start, end):
        result = compare_payloads(
            read_steps(source_client, day),
            read_steps(target_client, day),
            day,
        )
        store.append(
            CompareStateRecord(
                run_timestamp=run_timestamp,
                direction=direction,
                metric=METRIC_NAME,
                date=result.date,
                status=result.status,
                source_hash=result.source_hash,
                target_hash=result.target_hash,
                error=result.error,
            )
        )
        results.append(result)

    return results


def extract_total_steps(payload: Any) -> int | None:
    """Extract a daily totalSteps value from Garmin's daily steps payload."""

    if is_missing_payload(payload):
        return None
    if isinstance(payload, list):
        if not payload:
            return None
        first = payload[0]
        if not isinstance(first, dict):
            return None
        value = first.get("totalSteps")
    elif isinstance(payload, dict):
        value = payload.get("totalSteps")
    else:
        return None

    if value is None:
        return None
    return int(value)


def decide_steps_sync(source: MetricRead, target: MetricRead, day: date) -> SyncResult:
    """Apply the Global-to-CN steps sync gate for one date."""

    date_str = day.isoformat()
    if not source.ok or not target.ok:
        errors = [error for error in (source.error, target.error) if error]
        return SyncResult(
            date=date_str,
            status="read_error",
            source_steps=None,
            target_steps=None,
            error="; ".join(errors),
        )

    source_steps = extract_total_steps(source.payload)
    target_steps = extract_total_steps(target.payload)

    if source_steps is None:
        return SyncResult(
            date=date_str,
            status="sync_disabled",
            source_steps=None,
            target_steps=target_steps,
            error="Global steps are missing.",
        )

    target_for_compare = target_steps or 0
    if target_for_compare >= source_steps:
        return SyncResult(
            date=date_str,
            status="sync_disabled",
            source_steps=source_steps,
            target_steps=target_steps,
            steps_to_sync=0,
            error="CN steps are greater than or equal to Global steps.",
        )

    steps_to_sync = source_steps - target_for_compare
    return SyncResult(
        date=date_str,
        status="sync_unavailable",
        source_steps=source_steps,
        target_steps=target_steps,
        steps_to_sync=steps_to_sync,
        error=(
            "CN steps are lower than Global, but Garmin daily steps exposes no "
            "verified write endpoint. OPTIONS for "
            "/usersummary-service/stats/steps/daily/{date}/{date} allows only "
            "HEAD, GET, OPTIONS."
        ),
    )


def sync_steps_range(
    source_client: Any,
    target_client: Any,
    start: date,
    end: date,
    state_dir: Path,
    direction: str = GLOBAL_TO_CN,
) -> list[SyncResult]:
    """Evaluate steps sync eligibility for each day in an inclusive range."""

    validate_direction(direction)
    store = JsonlStateStore(state_dir, "steps_sync.jsonl")
    run_timestamp = utc_timestamp()
    results: list[SyncResult] = []

    for day in inclusive_dates(start, end):
        result = decide_steps_sync(
            read_steps(source_client, day),
            read_steps(target_client, day),
            day,
        )
        store.append(
            SyncStateRecord(
                run_timestamp=run_timestamp,
                direction=direction,
                metric=METRIC_NAME,
                date=result.date,
                status=result.status,
                source_steps=result.source_steps,
                target_steps=result.target_steps,
                steps_to_sync=result.steps_to_sync,
                error=result.error,
            )
        )
        results.append(result)

    return results
