"""Stable payload normalization and hashing."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Return a stable JSON representation for Garmin payload comparisons."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_hash(payload: Any) -> str:
    """Return a SHA-256 hash for a normalized payload."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def is_missing_payload(payload: Any) -> bool:
    """Return true when Garmin returned no usable payload for a metric/date."""

    return payload is None or payload == [] or payload == {}
