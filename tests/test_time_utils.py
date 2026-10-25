from datetime import datetime, timedelta

from src.time_utils import (
    AMSTERDAM_TZ,
    get_7day_rolling_window,
    is_in_7day_window,
    parse_dutch_datetime,
)


def test_get_7day_rolling_window_uses_now(monkeypatch):
    fixed_now = datetime(2026, 10, 25, 12, 0, tzinfo=AMSTERDAM_TZ)
    monkeypatch.setattr("src.time_utils.get_current_amsterdam_time", lambda: fixed_now)

    start, end = get_7day_rolling_window()
    assert start == fixed_now
    assert end == fixed_now + timedelta(days=7)


def test_is_in_7day_window_with_explicit_window():
    window_start = datetime(2026, 10, 25, 0, 0, tzinfo=AMSTERDAM_TZ)
    window_end = window_start + timedelta(days=7)

    assert is_in_7day_window(
        start=datetime(2026, 10, 26, 0, 0),
        end=datetime(2026, 10, 27, 0, 0),
        window_start=window_start,
        window_end=window_end,
    )
    assert not is_in_7day_window(
        start=datetime(2026, 11, 5, 0, 0, tzinfo=AMSTERDAM_TZ),
        end=None,
        window_start=window_start,
        window_end=window_end,
    )


def test_parse_dutch_datetime_iso_and_dutch_formats():
    iso_dt = parse_dutch_datetime("2026-10-25T08:30:00")
    dutch_dt = parse_dutch_datetime("25 oktober 2026 om 09:45 uur")

    assert iso_dt is not None and iso_dt.tzinfo == AMSTERDAM_TZ
    assert dutch_dt == datetime(2026, 10, 25, 9, 45, tzinfo=AMSTERDAM_TZ)


def test_parse_dutch_datetime_returns_none_for_invalid():
    assert parse_dutch_datetime("") is None
    assert parse_dutch_datetime("geen datum hier") is None
