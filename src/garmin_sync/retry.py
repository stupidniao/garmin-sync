"""Bounded retry helpers for safe Garmin reads."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import TypeVar

T = TypeVar("T")


def retry_read(operation: Callable[[], T]) -> T:
    """Run a safe read operation with bounded backoff."""

    delays = (0.5, 1.0, 2.0)
    last_error: Exception | None = None
    for attempt in range(len(delays) + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt == len(delays):
                break
            sleep(delays[attempt])
    assert last_error is not None
    raise last_error
