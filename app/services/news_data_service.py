# app/services/news_data_service.py
import re
import time
from datetime import datetime, timedelta
import investpy
from app.services.log_service import log_to_firestore

_cache = {}
CACHE_TTL = 30  # seconds

_day_cache = {}  # key: event_date -> {events: [...], ts: ...}
DAY_CACHE_TTL = 300  # 5 minutes — avoids redundant scrapes for same-day events


def get_day_cache():
    """Return a reference to the day cache (for invalidation from outside)."""
    return _day_cache


# Investpy country names for each currency we trade
CURRENCY_TO_COUNTRY = {
    "USD": "united states",
    "EUR": "euro zone",
    "GBP": "united kingdom",
    "JPY": "japan",
    "CHF": "switzerland",
    "CAD": "canada",
    "AUD": "australia",
    "NZD": "new zealand",
}

ALL_COUNTRIES = list(CURRENCY_TO_COUNTRY.values())


def parse_numeric_value(raw: str) -> float | None:
    """Parse economic values like '263K', '-0.3%', '3.50%', '1.234M', '2.5B'."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip().replace(",", "")

    multiplier = 1.0
    is_percent = False

    if raw.endswith("%"):
        raw = raw[:-1]
        is_percent = True
    elif raw.upper().endswith("K"):
        raw = raw[:-1]
        multiplier = 1_000
    elif raw.upper().endswith("M"):
        raw = raw[:-1]
        multiplier = 1_000_000
    elif raw.upper().endswith("B"):
        raw = raw[:-1]
        multiplier = 1_000_000_000
    elif raw.upper().endswith("T"):
        raw = raw[:-1]
        multiplier = 1_000_000_000_000

    try:
        value = float(raw) * multiplier
        if is_percent:
            return round(value, 4)
        return value
    except ValueError:
        return None


def calculate_surprise(actual: float, forecast: float) -> dict:
    """Calculate surprise direction and magnitude from actual vs forecast."""
    if actual is None or forecast is None:
        return {"surprise": None, "direction": "UNKNOWN", "magnitude": "UNKNOWN"}

    diff = actual - forecast

    # Direction
    if abs(diff) < 1e-9:
        direction = "INLINE"
    elif diff > 0:
        direction = "ABOVE"
    else:
        direction = "BELOW"

    # Magnitude based on % deviation from forecast
    if forecast == 0:
        abs_diff = abs(diff)
        if abs_diff < 0.1:
            magnitude = "SMALL"
        elif abs_diff < 1.0:
            magnitude = "MEDIUM"
        else:
            magnitude = "LARGE"
    else:
        pct_dev = abs(diff / forecast) * 100
        if pct_dev < 5:
            magnitude = "SMALL"
        elif pct_dev < 15:
            magnitude = "MEDIUM"
        else:
            magnitude = "LARGE"

    return {
        "surprise": round(diff, 4),
        "direction": direction,
        "magnitude": magnitude,
        "actual": actual,
        "forecast": forecast,
        "pct_deviation": round(abs(diff / forecast) * 100, 2) if forecast != 0 else None,
    }


def _fetch_investing_day_events(event_date: str) -> list:
    """Fetch and cache all high-impact events from Investing.com for a given date.

    Uses a 5-minute cache so that multiple events at the same time
    (e.g. CPI + NFP + Claims all at 18:30) share a single scrape call.

    Args:
        event_date: "YYYY-MM-DD" format
    """
    now = time.time()

    if event_date in _day_cache and now - _day_cache[event_date]["ts"] < DAY_CACHE_TTL:
        return _day_cache[event_date]["events"]

    # Convert YYYY-MM-DD to DD/MM/YYYY for investpy
    dt = datetime.strptime(event_date, "%Y-%m-%d")
    from_date = dt.strftime("%d/%m/%Y")
    # investpy requires to_date > from_date
    to_date = (dt + timedelta(days=1)).strftime("%d/%m/%Y")

    df = investpy.news.economic_calendar(
        time_zone="GMT",
        countries=ALL_COUNTRIES,
        importances=["high"],
        from_date=from_date,
        to_date=to_date,
    )

    # Convert DataFrame to list of dicts and filter to requested date only
    target_date_str = dt.strftime("%d/%m/%Y")
    events = []
    for _, row in df.iterrows():
        if row.get("date") != target_date_str:
            continue
        events.append({
            "event": row.get("event", ""),
            "currency": row.get("currency", ""),
            "actual": row.get("actual"),
            "forecast": row.get("forecast"),
            "previous": row.get("previous"),
            "importance": row.get("importance", ""),
            "time": row.get("time", ""),
        })

    # Don't cache if any high-impact event is missing its actual value
    # (it may be in the process of being published on Investing.com)
    missing_actuals = any(
        not str(ev.get("actual") or "").strip() or str(ev.get("actual")) == "None"
        for ev in events
    )
    if missing_actuals:
        log_to_firestore(
            f"[NewsData] {len(events)} events for {event_date} (not cached — some actuals missing)",
            level="INFO"
        )
    else:
        _day_cache[event_date] = {"events": events, "ts": now}
        log_to_firestore(
            f"[NewsData] Cached {len(events)} Investing.com events for {event_date}",
            level="INFO"
        )
    return events


def fetch_actual_value(event_title: str, country: str, event_date: str) -> dict:
    """
    Fetch actual value from Investing.com for a specific event.

    Args:
        event_title: e.g. "Nonfarm Payrolls", "CPI m/m"
        country: e.g. "USD", "EUR"
        event_date: e.g. "2026-02-07"

    Returns:
        {actual, forecast, previous, success}
    """
    cache_key = f"{event_title}_{country}_{event_date}"
    now = time.time()

    if cache_key in _cache:
        cached = _cache[cache_key]
        if now - cached["ts"] < CACHE_TTL:
            return cached["data"]

    try:
        events = _fetch_investing_day_events(event_date)

        for ev in events:
            ev_title = ev.get("event", "")
            ev_currency = (ev.get("currency") or "").upper()

            # Match by title and currency
            if not _fuzzy_match(event_title, ev_title):
                continue
            if country and ev_currency != country.upper():
                continue

            actual_raw = str(ev.get("actual") or "").strip()
            forecast_raw = str(ev.get("forecast") or "").strip()
            previous_raw = str(ev.get("previous") or "").strip()

            # Skip if no actual value yet
            if not actual_raw or actual_raw in ("", "None"):
                continue

            result = {
                "actual": parse_numeric_value(actual_raw),
                "forecast": parse_numeric_value(forecast_raw),
                "previous": parse_numeric_value(previous_raw),
                "actual_raw": actual_raw,
                "forecast_raw": forecast_raw,
                "previous_raw": previous_raw,
                "investing_event": ev_title,
                "investing_currency": ev_currency,
                "success": True,
            }

            _cache[cache_key] = {"data": result, "ts": now}
            log_to_firestore(
                f"[NewsData] Fetched {event_title}: actual={actual_raw}, forecast={forecast_raw}",
                level="INFO"
            )
            return result

        # Event not found or actual not yet published — don't cache failures
        return {"actual": None, "forecast": None, "previous": None, "success": False}

    except Exception as e:
        log_to_firestore(f"[NewsData] Investing.com scrape error for {event_title}: {e}", level="ERROR")
        return {"actual": None, "forecast": None, "previous": None, "success": False}


def _fuzzy_match(target: str, candidate: str) -> bool:
    """Simple fuzzy matching: check if key words from target appear in candidate."""
    target_lower = target.lower().strip()
    candidate_lower = candidate.lower().strip()

    if target_lower in candidate_lower or candidate_lower in target_lower:
        return True

    # Check if all significant words match
    noise = {"m/m", "y/y", "q/q", "of", "the", "and", "for", "(mom)", "(yoy)", "(qoq)"}
    target_words = set(re.split(r"\s+", target_lower)) - noise
    candidate_words = set(re.split(r"\s+", candidate_lower)) - noise
    if target_words and target_words.issubset(candidate_words):
        return True

    return False
