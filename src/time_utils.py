import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

AMSTERDAM_TZ = ZoneInfo("Europe/Amsterdam")

DUTCH_MONTHS = {
    "januari": 1, "jan": 1,
    "februari": 2, "feb": 2,
    "maart": 3, "mrt": 3,
    "april": 4, "apr": 4,
    "mei": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "augustus": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def get_current_amsterdam_time() -> datetime:
    return datetime.now(AMSTERDAM_TZ)


def get_7day_rolling_window() -> Tuple[datetime, datetime]:
    now = get_current_amsterdam_time()
    return now, now + timedelta(days=7)


def is_in_7day_window(
    start: Optional[datetime],
    end: Optional[datetime],
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> bool:
    if window_start is None or window_end is None:
        window_start, window_end = get_7day_rolling_window()

    if start and start.tzinfo is None:
        start = start.replace(tzinfo=AMSTERDAM_TZ)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=AMSTERDAM_TZ)

    if end and end < window_start:
        return False

    if start and start > window_end:
        return False

    return True


def parse_dutch_datetime(text: str, default_year: Optional[int] = None) -> Optional[datetime]:
    text = text.strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=AMSTERDAM_TZ)
        return dt.astimezone(AMSTERDAM_TZ)
    except ValueError:
        pass

    now = get_current_amsterdam_time()
    year = default_year or now.year

    pattern_full = r"(\d{1,2})\s+([a-zA-Z]+)(?:\s+(\d{4}))?(?:\s+(?:om\s+)?(\d{1,2})[:.](\d{2})(?:\s*uur)?)?"
    match = re.search(pattern_full, text, re.IGNORECASE)
    if match:
        day_str, month_str, year_str, hour_str, min_str = match.groups()
        month = DUTCH_MONTHS.get(month_str.lower())
        if month:
            day = int(day_str)
            target_year = int(year_str) if year_str else year
            hour = int(hour_str) if hour_str else 0
            minute = int(min_str) if min_str else 0
            try:
                return datetime(target_year, month, day, hour, minute, tzinfo=AMSTERDAM_TZ)
            except ValueError:
                return None

    return None
