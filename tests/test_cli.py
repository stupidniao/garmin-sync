import json

import garmin_sync.cli as cli
from garmin_sync.config import AccountConfig, AppConfig
from garmin_sync.steps import CompareResult

main = cli.main


def test_cli_help_for_compare_steps(capsys) -> None:
    try:
        main(["wellness", "compare-steps", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "compare-steps" in output
    assert "--start" in output
    assert "--end" in output
    assert "--profile" in output
    assert "--dry-run" in output
    assert "--audit-log" in output


def test_cli_help_for_sync_steps(capsys) -> None:
    try:
        main(["wellness", "sync-steps", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-steps" in output
    assert "--start" in output
    assert "--end" in output
    assert "--profile" in output
    assert "--dry-run" in output
    assert "--audit-log" in output


def test_cli_help_for_sync_schedule(capsys) -> None:
    try:
        main(["training", "sync-schedule", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-schedule" in output
    assert "--dry-run" in output
    assert "--force" in output
    assert "--profile" in output
    assert "--audit-log" in output


def test_cli_help_for_sync_today_activity(capsys) -> None:
    try:
        main(["activity", "sync-today", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-today" in output
    assert "--dry-run" in output
    assert "--date" in output
    assert "--profile" in output
    assert "--audit-log" in output


def test_cli_rejects_unsupported_direction(capsys) -> None:
    exit_code = main(
        [
            "wellness",
            "compare-steps",
            "--direction",
            "bidirectional",
            "--start",
            "2026-06-11",
            "--end",
            "2026-06-11",
        ]
    )

    assert exit_code == 2
    assert "Only global_to_cn" in capsys.readouterr().err


def test_cli_reports_missing_config(capsys) -> None:
    exit_code = main(
        [
            "wellness",
            "compare-steps",
            "--start",
            "2026-06-11",
            "--end",
            "2026-06-11",
            "--config",
            "missing.yml",
        ]
    )

    assert exit_code == 2
    assert "Config file not found" in capsys.readouterr().err


def test_cli_writes_structured_audit_log(monkeypatch, tmp_path, capsys) -> None:
    config = AppConfig(
        profile="default",
        global_account=AccountConfig(
            email="global@example.com",
            password="global-password",
            tokenstore=tmp_path / "tokens/global",
            is_cn=False,
        ),
        cn_account=AccountConfig(
            email="cn@example.com",
            password="cn-password",
            tokenstore=tmp_path / "tokens/cn",
            is_cn=True,
        ),
        state_dir=tmp_path,
    )
    audit_path = tmp_path / "audit.jsonl"

    monkeypatch.setattr(cli, "load_config", lambda path, profile=None: config)
    monkeypatch.setattr(cli, "login", lambda account: object())
    monkeypatch.setattr(
        cli,
        "compare_steps_range",
        lambda **kwargs: [
            CompareResult(
                date="2026-06-11",
                status="same",
                source_hash="source-hash",
                target_hash="source-hash",
            )
        ],
    )

    exit_code = main(
        [
            "wellness",
            "compare-steps",
            "--start",
            "2026-06-11",
            "--end",
            "2026-06-11",
            "--dry-run",
            "--audit-log",
            str(audit_path),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Wrote audit log" in output
    assert "Dry run: skipped compare state write" in output
    rows = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "run_started",
        "result",
        "run_completed",
    ]
    assert rows[0]["dry_run"] is True
    assert rows[1]["result"]["status"] == "same"
    assert "password" not in json.dumps(rows)


def test_cli_passes_persist_false_for_dry_run(monkeypatch, tmp_path) -> None:
    config = AppConfig(
        profile="default",
        global_account=AccountConfig(
            email="global@example.com",
            password="global-password",
            tokenstore=tmp_path / "tokens/global",
            is_cn=False,
        ),
        cn_account=AccountConfig(
            email="cn@example.com",
            password="cn-password",
            tokenstore=tmp_path / "tokens/cn",
            is_cn=True,
        ),
        state_dir=tmp_path,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "load_config", lambda path, profile=None: config)
    monkeypatch.setattr(cli, "login", lambda account: object())

    def fake_compare_steps_range(**kwargs):
        captured.update(kwargs)
        return [
            CompareResult(
                date="2026-06-11",
                status="same",
                source_hash="source-hash",
                target_hash="source-hash",
            )
        ]

    monkeypatch.setattr(cli, "compare_steps_range", fake_compare_steps_range)

    exit_code = main(
        [
            "wellness",
            "compare-steps",
            "--start",
            "2026-06-11",
            "--end",
            "2026-06-11",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert captured["persist"] is False
