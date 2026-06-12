from datetime import date

import pytest

from garmin_sync.dates import add_months, inclusive_dates, month_keys, parse_date


def test_parse_date_accepts_iso_date() -> None:
    assert parse_date("2026-06-11") == date(2026, 6, 11)


def test_inclusive_dates_includes_end_date() -> None:
    assert list(inclusive_dates(date(2026, 6, 10), date(2026, 6, 12))) == [
        date(2026, 6, 10),
        date(2026, 6, 11),
        date(2026, 6, 12),
    ]


def test_inclusive_dates_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="start date"):
        list(inclusive_dates(date(2026, 6, 12), date(2026, 6, 10)))


def test_add_months_clamps_to_month_end() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 6, 11), 1) == date(2026, 7, 11)


def test_month_keys_covers_cross_month_range() -> None:
    assert list(month_keys(date(2026, 6, 11), date(2026, 7, 11))) == [
        (2026, 6),
        (2026, 7),
    ]
