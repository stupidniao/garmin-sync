from garmin_sync.cli import main


def test_cli_help_for_compare_steps(capsys) -> None:
    try:
        main(["wellness", "compare-steps", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "compare-steps" in output
    assert "--start" in output
    assert "--end" in output


def test_cli_help_for_sync_steps(capsys) -> None:
    try:
        main(["wellness", "sync-steps", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-steps" in output
    assert "--start" in output
    assert "--end" in output


def test_cli_help_for_sync_schedule(capsys) -> None:
    try:
        main(["training", "sync-schedule", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-schedule" in output
    assert "--dry-run" in output
    assert "--force" in output


def test_cli_help_for_sync_today_activity(capsys) -> None:
    try:
        main(["activity", "sync-today", "--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "sync-today" in output
    assert "--dry-run" in output
    assert "--date" in output


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
