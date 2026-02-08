# app/services/calendar_service.py
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz

_cache = {"data": None, "fetched_at": None}
CACHE_TTL = 900  # 15 min

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"


def _fetch_calendar():
    now = datetime.now()
    if _cache["data"] and _cache["fetched_at"] and (now - _cache["fetched_at"]).seconds < CACHE_TTL:
        return _cache["data"]
    resp = requests.get(FF_URL, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    events = []
    for event in root.findall(".//event"):
        events.append({
            "title": event.findtext("title", ""),
            "country": event.findtext("country", ""),
            "date": event.findtext("date", ""),
            "time": event.findtext("time", ""),
            "impact": event.findtext("impact", ""),
            "forecast": event.findtext("forecast", ""),
            "previous": event.findtext("previous", ""),
        })
    _cache["data"] = events
    _cache["fetched_at"] = now
    return events


def _parse_event_datetime(event):
    """Parse ForexFactory date (MM-DD-YYYY) + time (h:mmam ET) to UTC datetime."""
    date_str = event["date"]
    time_str = event["time"]
    if not date_str or not time_str or time_str in ("", "All Day", "Tentative"):
        return None
    et = pytz.timezone("America/New_York")
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p")
    except ValueError:
        return None
    return et.localize(dt).astimezone(pytz.utc)


def check_high_impact_nearby(oanda_instrument: str, window_minutes=60) -> bool:
    currencies = oanda_instrument.split("_")  # ["USD", "CHF"]
    try:
        events = _fetch_calendar()
    except Exception:
        return False  # en cas d'erreur, ne pas bloquer le trade
    now = datetime.now(pytz.utc)
    for ev in events:
        if ev["impact"] != "High":
            continue
        if ev["country"].upper() not in currencies:
            continue
        ev_time = _parse_event_datetime(ev)
        if ev_time and abs((ev_time - now).total_seconds()) < window_minutes * 60:
            return True
    return False


def get_upcoming_events(oanda_instrument: str) -> list:
    currencies = oanda_instrument.split("_")
    try:
        events = _fetch_calendar()
    except Exception:
        return []
    now = datetime.now(pytz.utc)
    relevant = []
    for ev in events:
        if ev["country"].upper() not in currencies:
            continue
        ev_time = _parse_event_datetime(ev)
        if ev_time and ev_time > now - timedelta(hours=2):
            relevant.append({**ev, "datetime_utc": ev_time.isoformat() if ev_time else None})
    return relevant[:10]


def get_all_upcoming_events() -> list:
    """Retourne tous les events du jour, toutes devises confondues."""
    try:
        events = _fetch_calendar()
    except Exception:
        return []
    now = datetime.now(pytz.utc)
    relevant = []
    for ev in events:
        ev_time = _parse_event_datetime(ev)
        if ev_time and ev_time > now - timedelta(hours=4):
            relevant.append({**ev, "datetime_utc": ev_time.isoformat() if ev_time else None})
    return relevant[:30]
