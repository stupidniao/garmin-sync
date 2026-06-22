import json

from garmin_sync.audit import AuditLogger, default_audit_log_path


def test_audit_logger_appends_jsonl_events(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(
        path=path,
        command="wellness.compare-steps",
        profile="default",
        run_id="run-1",
    )

    logger.log("run_started", dry_run=True)
    logger.log("run_completed", result_count=0)

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["command"] == "wellness.compare-steps"
    assert rows[0]["profile"] == "default"
    assert rows[0]["dry_run"] is True
    assert rows[1]["event"] == "run_completed"


def test_default_audit_log_path_is_profile_scoped(tmp_path) -> None:
    assert default_audit_log_path(tmp_path) == tmp_path / "audit.jsonl"
