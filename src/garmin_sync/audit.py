"""Structured audit logging for Garmin sync commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AuditLogger:
    """Append-only JSONL audit logger for one CLI run."""

    path: Path
    command: str
    profile: str
    run_id: str = field(default_factory=lambda: uuid4().hex)

    def log(self, event: str, **fields: Any) -> None:
        """Append one structured audit event."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": _timestamp(),
            "run_id": self.run_id,
            "profile": self.profile,
            "command": self.command,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            json.dump(row, handle, sort_keys=True)
            handle.write("\n")


def default_audit_log_path(state_dir: Path) -> Path:
    """Return the default profile-scoped audit log path."""

    return state_dir / "audit.jsonl"


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
