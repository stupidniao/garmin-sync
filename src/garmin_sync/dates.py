"""Date parsing and inclusive range helpers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta


def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD date string."""

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def inclusive_dates(start: date, end: date) -> Iterator[date]:
    """Yield each date from start through end, inclusive."""

    if start > end:
        raise ValueError("start date must be before or equal to end date")

    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def add_months(day: date, months: int) -> date:
    """Return date shifted by whole calendar months."""

    if months < 0:
        raise ValueError("months must be non-negative")

    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    max_day = _days_in_month(year, month)
    return date(year, month, min(day.day, max_day))


def month_keys(start: date, end: date) -> Iterator[tuple[int, int]]:
    """Yield year/month pairs covered by an inclusive date range."""

    if start > end:
        raise ValueError("start date must be before or equal to end date")

    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day
