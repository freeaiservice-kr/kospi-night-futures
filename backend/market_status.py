import logging
from datetime import datetime, date, timedelta
from backend.config import settings
from backend.models import MarketStatus

logger = logging.getLogger(__name__)

# Static 2026 KRX holidays (Korean public holidays + market closures)
KRX_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 26),  # Lunar New Year Eve
    date(2026, 1, 27),  # Lunar New Year
    date(2026, 1, 28),  # Lunar New Year (holiday)
    date(2026, 3, 1),   # Independence Movement Day
    date(2026, 5, 5),   # Children's Day
    date(2026, 5, 15),  # Buddha's Birthday
    date(2026, 6, 6),   # Memorial Day
    date(2026, 8, 15),  # Liberation Day
    date(2026, 9, 24),  # Chuseok Eve
    date(2026, 9, 25),  # Chuseok
    date(2026, 9, 26),  # Chuseok (holiday)
    date(2026, 10, 3),  # National Foundation Day
    date(2026, 10, 9),  # Hangul Day
    date(2026, 12, 25), # Christmas
    date(2026, 12, 31), # Year-end market closure
}


def get_session_start_ts() -> int:
    """
    Return unix timestamp (seconds) of the current night session start.
    Mirrors the session_start_date logic in get_market_status() using naive local time.
    """
    now = datetime.now()
    open_h, open_m = _parse_time(settings.night_session_open)
    open_minutes = open_h * 60 + open_m
    current_minutes = now.hour * 60 + now.minute

    if current_minutes >= open_minutes:
        session_start_date = now.date()
    else:
        session_start_date = now.date() - timedelta(days=1)

    session_start_dt = datetime(
        session_start_date.year,
        session_start_date.month,
        session_start_date.day,
        open_h, open_m, 0,
    )
    return int(session_start_dt.timestamp())


def _parse_time(time_str: str):
    """Parse HH:MM string to (hour, minute) tuple."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def get_market_status(now: datetime | None = None) -> MarketStatus:
    """
    Determine the current night futures market session status.

    Night session: 18:00 ~ 05:00 KST (next day)
    Pre-open auction: 17:50 ~ 18:00
    Pre-close auction: 04:50 ~ 05:00
    Closed on weekends and KRX holidays.
    """
    if now is None:
        now = datetime.now()

    today = now.date()
    h, m = now.hour, now.minute
    current_minutes = h * 60 + m

    open_h, open_m = _parse_time(settings.night_session_open)
    close_h, close_m = _parse_time(settings.night_session_close)

    open_minutes = open_h * 60 + open_m      # e.g., 18*60 = 1080
    close_minutes = close_h * 60 + close_m    # e.g., 5*60 = 300

    pre_open_start = open_minutes - 10        # 17:50
    pre_close_start = close_minutes - 10      # 04:50

    # Night session spans midnight: open > close in minutes
    # Session is active if: time >= open OR time < close (crosses midnight)
    in_night = (current_minutes >= open_minutes) or (current_minutes < close_minutes)
    in_pre_open = (pre_open_start <= current_minutes < open_minutes)
    in_pre_close = (pre_close_start <= current_minutes < close_minutes)

    # Weekend check: night session starts on weekday evening, closes next morning
    # If it's before close (e.g., 02:00 Sat), the session started on Fri evening
    # If it's after open (e.g., 20:00 Sat), session would start Sat but KRX is closed
    session_start_date = today if current_minutes >= open_minutes else today - timedelta(days=1)

    is_holiday_or_weekend = (
        session_start_date.weekday() >= 5  # Saturday=5, Sunday=6
        or session_start_date in KRX_HOLIDAYS_2026
    )

    if is_holiday_or_weekend and in_night:
        return MarketStatus(
            is_open=False,
            session_name="closed",
            next_open=_next_open_time(now, open_h, open_m),
        )

    if in_pre_open:
        return MarketStatus(
            is_open=False,
            session_name="auction_pre",
            next_open=now.replace(hour=open_h, minute=open_m, second=0, microsecond=0),
        )

    if in_pre_close:
        return MarketStatus(
            is_open=True,
            session_name="auction_close",
            next_close=now.replace(hour=close_h, minute=close_m, second=0, microsecond=0),
        )

    if in_night and not is_holiday_or_weekend:
        next_close = _compute_close_time(now, close_h, close_m)
        return MarketStatus(
            is_open=True,
            session_name="night",
            next_close=next_close,
        )

    # Day session or between sessions
    return MarketStatus(
        is_open=False,
        session_name="day",
        next_open=_next_open_time(now, open_h, open_m),
    )


def _compute_close_time(now: datetime, close_h: int, close_m: int) -> datetime:
    """Compute next close time (may be next day if session opened before midnight)."""
    candidate = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    if now.hour >= 18:
        # Close is next calendar day
        candidate = candidate + timedelta(days=1)
    return candidate


def _next_open_time(now: datetime, open_h: int, open_m: int) -> datetime:
    """Find the next night session open time, skipping weekends and holidays."""
    candidate = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    if now >= candidate:
        candidate += timedelta(days=1)

    # Skip weekends and holidays (up to 10 days)
    for _ in range(10):
        if candidate.weekday() < 5 and candidate.date() not in KRX_HOLIDAYS_2026:
            return candidate
        candidate += timedelta(days=1)

    return candidate


def get_options_market_status(now: datetime | None = None) -> MarketStatus:
    """
    Day session options market: 08:45 ~ 15:45 KST weekdays.
    Pre-open: 08:00 ~ 08:45. Expiry day close: 15:20 (not tracked here).
    """
    if now is None:
        now = datetime.now()

    today = now.date()
    current_minutes = now.hour * 60 + now.minute

    open_minutes = 8 * 60 + 45   # 08:45
    close_minutes = 15 * 60 + 45  # 15:45

    is_weekend = today.weekday() >= 5
    is_holiday = today in KRX_HOLIDAYS_2026

    if is_weekend or is_holiday:
        next_open_candidate = now.replace(hour=8, minute=45, second=0, microsecond=0)
        if now >= next_open_candidate:
            next_open_candidate = next_open_candidate.replace(day=today.day + 1)
        return MarketStatus(is_open=False, session_name="closed", next_open=next_open_candidate)

    in_session = open_minutes <= current_minutes < close_minutes

    if in_session:
        next_close = now.replace(hour=15, minute=45, second=0, microsecond=0)
        return MarketStatus(is_open=True, session_name="day", next_close=next_close)

    next_open = now.replace(hour=8, minute=45, second=0, microsecond=0)
    if current_minutes >= close_minutes:
        from datetime import timedelta
        next_open += timedelta(days=1)
        while next_open.weekday() >= 5 or next_open.date() in KRX_HOLIDAYS_2026:
            next_open += timedelta(days=1)
    return MarketStatus(is_open=False, session_name="closed", next_open=next_open)
