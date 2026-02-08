# app/services/news_data_service.py
import re
import time
import requests
from bs4 import BeautifulSoup
from app.services.log_service import log_to_firestore

_cache = {}
CACHE_TTL = 30  # seconds

INVESTING_CALENDAR_URL = "https://www.investing.com/economic-calendar/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
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
        # Avoid division by zero — use absolute diff thresholds
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


def scrape_actual_value(event_title: str, country: str, event_date: str) -> dict:
    """
    Scrape Investing.com economic calendar for the actual value of a specific event.

    Args:
        event_title: e.g. "Nonfarm Payrolls", "CPI m/m"
        country: e.g. "USD", "EUR"
        event_date: e.g. "2025-01-10"

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
        resp = requests.get(INVESTING_CALENDAR_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html5lib")

        # Investing.com calendar uses a table with rows per event
        rows = soup.select("tr.js-event-item")

        for row in rows:
            title_el = row.select_one("td.event a")
            if not title_el:
                continue
            row_title = title_el.get_text(strip=True)

            # Fuzzy match on event title
            if not _fuzzy_match(event_title, row_title):
                continue

            # Check country via flag class
            flag_el = row.select_one("td.flagCur span")
            if flag_el:
                row_country = flag_el.get("title", "").strip()
            else:
                row_country = ""

            # Extract values
            actual_el = row.select_one("td.act")
            forecast_el = row.select_one("td.fore")
            previous_el = row.select_one("td.prev")

            actual_raw = actual_el.get_text(strip=True) if actual_el else ""
            forecast_raw = forecast_el.get_text(strip=True) if forecast_el else ""
            previous_raw = previous_el.get_text(strip=True) if previous_el else ""

            # Skip if no actual value published yet
            if not actual_raw or actual_raw == "\xa0":
                continue

            result = {
                "actual": parse_numeric_value(actual_raw),
                "forecast": parse_numeric_value(forecast_raw),
                "previous": parse_numeric_value(previous_raw),
                "actual_raw": actual_raw,
                "forecast_raw": forecast_raw,
                "previous_raw": previous_raw,
                "success": True,
            }

            _cache[cache_key] = {"data": result, "ts": now}
            log_to_firestore(
                f"[NewsData] Scraped {event_title}: actual={actual_raw}, forecast={forecast_raw}",
                level="INFO"
            )
            return result

        # Event not found or actual not yet published
        result = {"actual": None, "forecast": None, "previous": None, "success": False}
        _cache[cache_key] = {"data": result, "ts": now}
        return result

    except Exception as e:
        log_to_firestore(f"[NewsData] Scrape error for {event_title}: {e}", level="ERROR")
        return {"actual": None, "forecast": None, "previous": None, "success": False}


def _fuzzy_match(target: str, candidate: str) -> bool:
    """Simple fuzzy matching: check if key words from target appear in candidate."""
    target_lower = target.lower().strip()
    candidate_lower = candidate.lower().strip()

    if target_lower in candidate_lower or candidate_lower in target_lower:
        return True

    # Check if all significant words match
    target_words = set(re.split(r"\s+", target_lower)) - {"m/m", "y/y", "q/q", "of", "the", "and", "for"}
    candidate_words = set(re.split(r"\s+", candidate_lower))
    if target_words and target_words.issubset(candidate_words):
        return True

    return False
