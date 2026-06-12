"""Command-line interface for Garmin sync."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from garmin_sync.activity import CN_TO_GLOBAL, sync_activities_for_date
from garmin_sync.config import ConfigError, load_config
from garmin_sync.dates import add_months, parse_date
from garmin_sync.garmin_client import login
from garmin_sync.steps import (
    GLOBAL_TO_CN,
    compare_steps_range,
    sync_steps_range,
    validate_direction,
)
from garmin_sync.training import sync_training_schedule_range


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-sync")
    subparsers = parser.add_subparsers(dest="scope", required=True)

    wellness = subparsers.add_parser("wellness", help="Wellness metric commands")
    wellness_subparsers = wellness.add_subparsers(dest="command", required=True)

    training = subparsers.add_parser("training", help="Training schedule commands")
    training_subparsers = training.add_subparsers(dest="command", required=True)

    activity = subparsers.add_parser("activity", help="Activity sync commands")
    activity_subparsers = activity.add_subparsers(dest="command", required=True)

    compare_steps = wellness_subparsers.add_parser(
        "compare-steps",
        help="Compare daily steps between Garmin International and Garmin China",
    )
    compare_steps.add_argument("--direction", default=GLOBAL_TO_CN)
    compare_steps.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    compare_steps.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    compare_steps.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path",
    )
    compare_steps.add_argument(
        "--config",
        type=Path,
        default=Path("config.yml"),
        help="YAML config path, defaults to config.yml",
    )

    sync_steps = wellness_subparsers.add_parser(
        "sync-steps",
        help="Evaluate daily steps sync from Garmin International to Garmin China",
    )
    sync_steps.add_argument("--direction", default=GLOBAL_TO_CN)
    sync_steps.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    sync_steps.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    sync_steps.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path",
    )
    sync_steps.add_argument(
        "--config",
        type=Path,
        default=Path("config.yml"),
        help="YAML config path, defaults to config.yml",
    )

    sync_schedule = training_subparsers.add_parser(
        "sync-schedule",
        help="Sync scheduled workouts from Garmin International to Garmin China",
    )
    sync_schedule.add_argument("--direction", default=GLOBAL_TO_CN)
    sync_schedule.add_argument("--start", help="Start date, YYYY-MM-DD; defaults to today")
    sync_schedule.add_argument(
        "--end",
        help="End date, YYYY-MM-DD; defaults to same day next month",
    )
    sync_schedule.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate sync actions without uploading or scheduling workouts",
    )
    sync_schedule.add_argument(
        "--force",
        action="store_true",
        help="Bypass state and same-day same-name duplicate checks",
    )
    sync_schedule.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path",
    )
    sync_schedule.add_argument(
        "--config",
        type=Path,
        default=Path("config.yml"),
        help="YAML config path, defaults to config.yml",
    )

    sync_today_activity = activity_subparsers.add_parser(
        "sync-today",
        help="Sync today's activities from Garmin China to Garmin International",
    )
    sync_today_activity.add_argument("--direction", default=CN_TO_GLOBAL)
    sync_today_activity.add_argument(
        "--date",
        help="Date to sync, YYYY-MM-DD; defaults to today",
    )
    sync_today_activity.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate sync actions without downloading or uploading activities",
    )
    sync_today_activity.add_argument(
        "--force",
        action="store_true",
        help="Bypass state and same-start-time duplicate checks",
    )
    sync_today_activity.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path",
    )
    sync_today_activity.add_argument(
        "--config",
        type=Path,
        default=Path("config.yml"),
        help="YAML config path, defaults to config.yml",
    )

    return parser


def _write_report(path: Path, results: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _run_compare_steps(args: argparse.Namespace) -> int:
    direction = validate_direction(args.direction)
    start = parse_date(args.start)
    end = parse_date(args.end)
    config = load_config(args.config)

    source_client = login(config.global_account)
    target_client = login(config.cn_account)
    results = compare_steps_range(
        source_client=source_client,
        target_client=target_client,
        start=start,
        end=end,
        state_dir=config.state_dir,
        direction=direction,
    )

    report_rows = [asdict(result) for result in results]
    for row in report_rows:
        error_suffix = f" error={row['error']}" if row.get("error") else ""
        print(
            "{date} steps {status} source={source_hash} target={target_hash}{error_suffix}".format(
                error_suffix=error_suffix,
                **row
            )
        )

    if args.output:
        _write_report(args.output, report_rows)
        print(f"Wrote JSON report: {args.output}")

    print(f"Wrote compare state: {config.state_dir / 'steps_compare.jsonl'}")
    return 0


def _run_sync_steps(args: argparse.Namespace) -> int:
    direction = validate_direction(args.direction)
    start = parse_date(args.start)
    end = parse_date(args.end)
    config = load_config(args.config)

    source_client = login(config.global_account)
    target_client = login(config.cn_account)
    results = sync_steps_range(
        source_client=source_client,
        target_client=target_client,
        start=start,
        end=end,
        state_dir=config.state_dir,
        direction=direction,
    )

    report_rows = [asdict(result) for result in results]
    for row in report_rows:
        error_suffix = f" error={row['error']}" if row.get("error") else ""
        print(
            "{date} steps {status} source_steps={source_steps} "
            "target_steps={target_steps} steps_to_sync={steps_to_sync}{error_suffix}".format(
                error_suffix=error_suffix,
                **row,
            )
        )

    if args.output:
        _write_report(args.output, report_rows)
        print(f"Wrote JSON report: {args.output}")

    print(f"Wrote sync state: {config.state_dir / 'steps_sync.jsonl'}")
    return 0


def _run_sync_schedule(args: argparse.Namespace) -> int:
    direction = validate_direction(args.direction)
    start = parse_date(args.start) if args.start else date.today()
    end = parse_date(args.end) if args.end else add_months(start, 1)
    config = load_config(args.config)

    source_client = login(config.global_account)
    target_client = login(config.cn_account)
    results = sync_training_schedule_range(
        source_client=source_client,
        target_client=target_client,
        start=start,
        end=end,
        state_dir=config.state_dir,
        direction=direction,
        dry_run=args.dry_run,
        force=args.force,
    )

    report_rows = [asdict(result) for result in results]
    for row in report_rows:
        error_suffix = f" error={row['error']}" if row.get("error") else ""
        print(
            "{date} training {status} source_schedule={source_scheduled_workout_id} "
            "source_workout={source_workout_id} target_workout={target_workout_id} "
            "name={workout_name}{error_suffix}".format(
                error_suffix=error_suffix,
                **row,
            )
        )

    if args.output:
        _write_report(args.output, report_rows)
        print(f"Wrote JSON report: {args.output}")

    print(f"Wrote training sync state: {config.state_dir / 'training_schedule_sync.jsonl'}")
    return 0


def _run_sync_today_activity(args: argparse.Namespace) -> int:
    if args.direction != CN_TO_GLOBAL:
        raise ValueError("Only cn_to_global is supported for activity sync v1")
    sync_date = parse_date(args.date) if args.date else date.today()
    config = load_config(args.config)

    source_client = login(config.cn_account)
    target_client = login(config.global_account)
    results = sync_activities_for_date(
        source_client=source_client,
        target_client=target_client,
        sync_date=sync_date,
        state_dir=config.state_dir,
        direction=args.direction,
        dry_run=args.dry_run,
        force=args.force,
    )

    report_rows = [asdict(result) for result in results]
    for row in report_rows:
        error_suffix = f" error={row['error']}" if row.get("error") else ""
        print(
            "{date} activity {status} source_activity={source_activity_id} "
            "target_activity={target_activity_id} start={start_time_local} "
            "name={activity_name}{error_suffix}".format(
                error_suffix=error_suffix,
                **row,
            )
        )

    if args.output:
        _write_report(args.output, report_rows)
        print(f"Wrote JSON report: {args.output}")

    print(f"Wrote activity sync state: {config.state_dir / 'activity_sync.jsonl'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the Garmin sync CLI."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.scope == "wellness" and args.command == "compare-steps":
            return _run_compare_steps(args)
        if args.scope == "wellness" and args.command == "sync-steps":
            return _run_sync_steps(args)
        if args.scope == "training" and args.command == "sync-schedule":
            return _run_sync_schedule(args)
        if args.scope == "activity" and args.command == "sync-today":
            return _run_sync_today_activity(args)
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
