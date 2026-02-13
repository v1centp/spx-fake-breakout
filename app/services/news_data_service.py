# app/services/news_data_service.py
import re
import time
import random
import requests
from app.services.log_service import log_to_firestore

_cache = {}
CACHE_TTL = 30  # seconds

_day_cache = {}  # key: event_date -> {events: [...], ts: ...}
DAY_CACHE_TTL = 300  # 5 minutes — avoids redundant scrapes for same-day events

_session = None
_session_ts = 0
_SESSION_TTL = 600  # refresh session cookies every 10 min


def get_day_cache():
    """Return a reference to the day cache (for invalidation from outside)."""
    return _day_cache


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

# Investing.com country IDs for calendar API
_INVESTING_COUNTRY_IDS = {
    "united states": 5,
    "euro zone": 72,
    "united kingdom": 4,
    "japan": 35,
    "switzerland": 12,
    "canada": 6,
    "australia": 25,
    "new zealand": 43,
}

_INVESTING_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
]
_INVESTING_BASE_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.investing.com/economic-calendar/",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.investing.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "sec-ch-ua-platform": '"Windows"',
}


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


def _parse_investing_row(row_html: str) -> dict | None:
    """Parse a single event row from Investing.com calendar HTML."""
    time_m = re.search(r'js-time[^>]*>\s*([\d:]+(?:\s*[APap][Mm])?)', row_html)
    curr_m = re.search(r'flagCur[^>]*>.*?</span>\s*(\w+)', row_html, re.DOTALL)
    event_m = re.search(r'class="left event"[^>]*>.*?>(.*?)</a>', row_html, re.DOTALL)
    actual_m = re.search(r'eventActual_\d+"[^>]*>(.*?)</td>', row_html, re.DOTALL)
    forecast_m = re.search(r'eventForecast_\d+"[^>]*>(.*?)</td>', row_html, re.DOTALL)
    previous_m = re.search(r'eventPrevious_\d+"[^>]*>(.*?)</td>', row_html, re.DOTALL)

    def _clean(m):
        if not m:
            return ""
        return re.sub(r'<[^>]+>', '', m.group(1)).replace('&nbsp;', '').strip()

    event_name = _clean(event_m)
    if not event_name:
        return None

    return {
        "event": event_name,
        "currency": _clean(curr_m).upper(),
        "actual": _clean(actual_m) or None,
        "forecast": _clean(forecast_m) or None,
        "previous": _clean(previous_m) or None,
        "importance": "high",
        "time": _clean(time_m),
    }


def _get_investing_session() -> requests.Session:
    """Return a requests.Session with fresh cookies from Investing.com.

    Investing.com sets anti-bot cookies on the initial page load.
    Re-using them in subsequent API calls prevents 403 errors.
    """
    global _session, _session_ts

    now = time.time()
    if _session and now - _session_ts < _SESSION_TTL:
        return _session

    s = requests.Session()
    ua = random.choice(_USER_AGENTS)
    s.headers.update({**_INVESTING_BASE_HEADERS, "User-Agent": ua})

    try:
        # Visit the calendar page to collect cookies
        s.get(
            "https://www.investing.com/economic-calendar/",
            headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            timeout=15,
        )
    except Exception:
        pass  # best-effort — POST may still work without cookies

    _session = s
    _session_ts = now
    return s


def _fetch_investing_day_events(event_date: str) -> list:
    """Fetch and cache all high-impact events from Investing.com for a given date.

    Uses a 5-minute cache so that multiple events at the same time
    (e.g. CPI + NFP + Claims all at 13:30) share a single HTTP call.

    Args:
        event_date: "YYYY-MM-DD" format
    """
    global _session, _session_ts

    now = time.time()

    if event_date in _day_cache and now - _day_cache[event_date]["ts"] < DAY_CACHE_TTL:
        return _day_cache[event_date]["events"]

    # Build POST params — all tracked countries, high importance only
    params = [("country[]", cid) for cid in _INVESTING_COUNTRY_IDS.values()]
    params.append(("importance[]", 3))
    params.extend([
        ("dateFrom", event_date),
        ("dateTo", event_date),
        ("timeZone", 8),        # GMT+0 display
        ("timeFilter", "timeOnly"),
        ("currentTab", "custom"),
        ("limit_from", 0),
    ])

    html = None
    last_error = None
    for attempt in range(3):
        session = _get_investing_session()
        try:
            resp = session.post(_INVESTING_URL, data=params, timeout=15)
            resp.raise_for_status()
            html = resp.json().get("data", "")
            break
        except requests.exceptions.HTTPError as e:
            last_error = e
            if resp.status_code == 403:
                # Force session refresh on next attempt
                _session = None
                _session_ts = 0
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
            raise
    if html is None:
        raise last_error

    # Parse each event row from the returned HTML
    events = []
    for row_html in re.findall(r'<tr id="eventRowId_\d+"[^>]*>(.*?)</tr>', html, re.DOTALL):
        ev = _parse_investing_row(row_html)
        if ev:
            events.append(ev)

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


# ForexFactory → Investing.com title aliases (FF names that differ significantly)
_EVENT_ALIASES = {
    "non-farm employment change": "nonfarm payrolls",
    "employment change": "nonfarm payrolls",
}


def _fuzzy_match(target: str, candidate: str) -> bool:
    """Fuzzy matching between ForexFactory and Investing.com event titles."""
    target_lower = target.lower().strip()
    candidate_lower = candidate.lower().strip()

    # Normalize: remove hyphens, parentheses, extra whitespace
    def _norm(s):
        return re.sub(r'\s+', ' ', s.replace("-", " ").replace("(", "").replace(")", "")).strip()

    target_n = _norm(target_lower)
    candidate_n = _norm(candidate_lower)

    if target_n in candidate_n or candidate_n in target_n:
        return True

    # Check known aliases
    alias = _EVENT_ALIASES.get(target_lower)
    if alias and alias in candidate_n:
        return True

    # Check if all significant words match
    noise = {"m/m", "y/y", "q/q", "of", "the", "and", "for", "mom", "yoy", "qoq"}
    target_words = set(target_n.split()) - noise
    candidate_words = set(candidate_n.split()) - noise
    if target_words and target_words.issubset(candidate_words):
        return True

    return False
