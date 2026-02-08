# app/services/news_scheduler.py
import re
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.calendar_service import _fetch_calendar, _parse_event_datetime
from app.services.news_data_service import scrape_actual_value, calculate_surprise
from app.services.news_analyzer import pre_release_analysis, post_release_decision
from app.services.calendar_service import get_all_upcoming_events
from app.services.log_service import log_to_firestore
from app.services.firebase import get_firestore
from app.strategies.news_trading_strategy import execute_news_trade

NEWS_TRADING_CONFIG = {
    "min_surprise_magnitude": "MEDIUM",
    "default_sl_pips": 15,
    "tp_ratio": 2.0,
    "max_hold_minutes": 30,
    "pre_analysis_offset_seconds": -120,   # T-2min
    "scrape_offset_seconds": 30,           # T+30s
    "trade_decision_offset_seconds": 120,  # T+2min
}

# Which instruments to trade for each currency
CURRENCY_INSTRUMENTS = {
    "USD": ["EUR_USD", "USD_JPY", "USD_CHF"],
    "EUR": ["EUR_USD", "EUR_GBP"],
    "GBP": ["GBP_USD", "EUR_GBP"],
    "JPY": ["USD_JPY", "EUR_JPY"],
    "CHF": ["USD_CHF"],
    "AUD": ["AUD_USD"],
    "NZD": ["NZD_USD"],
    "CAD": ["USD_CAD"],
}

# In-memory state for each event being tracked
_event_state = {}

# Scheduler instance
_scheduler = None


def _make_event_id(event: dict) -> str:
    """Create a unique ID for an event from its properties."""
    title = re.sub(r"[^a-zA-Z0-9]", "_", event["title"])[:30]
    country = event["country"].upper()
    time_str = event.get("time", "").replace(":", "").replace(" ", "")
    return f"{country}_{title}_{time_str}"


def _get_best_instrument(event: dict) -> str | None:
    """Pick the primary instrument to trade for a given event's currency."""
    country = event["country"].upper()
    instruments = CURRENCY_INSTRUMENTS.get(country, [])
    return instruments[0] if instruments else None


def _job_pre_analysis(event_id: str):
    """T-2min job: run GPT pre-release analysis."""
    state = _event_state.get(event_id)
    if not state:
        return

    event = state["event"]
    instrument = state["instrument"]

    log_to_firestore(
        f"[NewsScheduler] T-2min: pre-analysis for {event['title']} on {instrument}",
        level="INFO"
    )

    try:
        all_events = get_all_upcoming_events()
        analysis = pre_release_analysis(event, instrument, all_events)
        state["pre_analysis"] = analysis

        # Log to Firestore
        db = get_firestore()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{event_id}").set({
                "event_id": event_id,
                "title": event["title"],
                "country": event["country"],
                "instrument": instrument,
                "phase": "pre_analysis",
                "pre_analysis": analysis,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)

    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Pre-analysis error for {event_id}: {e}", level="ERROR")
        state["pre_analysis"] = {
            "bias": "NEUTRAL", "confidence": 0, "analysis": f"Error: {e}",
            "expected_direction_if_beat": "BULLISH",
            "expected_direction_if_miss": "BEARISH",
        }


def _job_scrape_actual(event_id: str):
    """T+30s job: scrape the actual value from Investing.com."""
    state = _event_state.get(event_id)
    if not state:
        return

    event = state["event"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log_to_firestore(
        f"[NewsScheduler] T+30s: scraping actual for {event['title']}",
        level="INFO"
    )

    try:
        scraped = scrape_actual_value(event["title"], event["country"], today)
        state["scraped"] = scraped

        if scraped["success"] and scraped["actual"] is not None:
            forecast = scraped["forecast"]
            # Fallback to ForexFactory forecast if Investing.com didn't have it
            if forecast is None:
                from app.services.news_data_service import parse_numeric_value
                forecast = parse_numeric_value(event.get("forecast", ""))
                scraped["forecast"] = forecast

            surprise = calculate_surprise(scraped["actual"], forecast)
            state["surprise"] = surprise

            log_to_firestore(
                f"[NewsScheduler] {event['title']}: actual={scraped['actual']}, "
                f"forecast={forecast}, surprise={surprise['direction']} ({surprise['magnitude']})",
                level="INFO"
            )
        else:
            state["surprise"] = {"direction": "UNKNOWN", "magnitude": "UNKNOWN", "surprise": None}
            log_to_firestore(
                f"[NewsScheduler] Failed to scrape actual for {event['title']}",
                level="WARN"
            )

        # Log to Firestore
        db = get_firestore()
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{event_id}").set({
                "phase": "scraped",
                "scraped": scraped,
                "surprise": state.get("surprise"),
                "scrape_timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)

    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Scrape error for {event_id}: {e}", level="ERROR")
        state["surprise"] = {"direction": "UNKNOWN", "magnitude": "UNKNOWN", "surprise": None}


def _job_trade_decision(event_id: str):
    """T+2min job: make trade decision and execute if conditions are met."""
    state = _event_state.get(event_id)
    if not state:
        return

    event = state["event"]
    instrument = state["instrument"]
    pre_analysis = state.get("pre_analysis", {"bias": "NEUTRAL", "confidence": 0})
    surprise = state.get("surprise", {"direction": "UNKNOWN", "magnitude": "UNKNOWN"})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log_to_firestore(
        f"[NewsScheduler] T+2min: trade decision for {event['title']} on {instrument}",
        level="INFO"
    )

    # Make decision
    decision = post_release_decision(event, surprise, pre_analysis, instrument)

    log_to_firestore(
        f"[NewsScheduler] Decision for {event['title']}: {decision['action']} - {decision['reason']}",
        level="INFO"
    )

    _log_decision_to_firestore(event_id, today, decision["action"], decision["reason"], state)

    if decision["action"] != "TRADE":
        return

    # Execute trade
    try:
        result = execute_news_trade(
            event=event,
            event_id=event_id,
            instrument=instrument,
            pre_analysis=pre_analysis,
            surprise=surprise,
            decision=decision,
        )

        if result["status"] == "EXECUTED":
            log_to_firestore(
                f"[NewsScheduler] Trade executed: {result['instrument']} {result['direction']}",
                level="TRADING"
            )
        else:
            log_to_firestore(
                f"[NewsScheduler] Trade not executed: {result.get('reason', 'unknown')}",
                level="INFO"
            )

    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Trade execution error: {e}", level="ERROR")


def _log_decision_to_firestore(event_id: str, today: str, action: str, reason: str, state: dict):
    """Log the trade decision to Firestore for analysis."""
    try:
        db = get_firestore()
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{event_id}").set({
                "phase": "decision",
                "decision_action": action,
                "decision_reason": reason,
                "decision_timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)
    except Exception:
        pass


def load_and_schedule_today():
    """Load today's high-impact events from ForexFactory and schedule jobs."""
    global _event_state

    try:
        events = _fetch_calendar()
    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Failed to fetch calendar: {e}", level="ERROR")
        return

    now = datetime.now(timezone.utc)
    scheduled_count = 0

    for event in events:
        if event["impact"] != "High":
            continue

        event_time = _parse_event_datetime(event)
        if not event_time:
            continue

        # Only schedule future events (allow up to 5 min in the past for scrape/decision)
        if event_time < now - timedelta(minutes=5):
            continue

        instrument = _get_best_instrument(event)
        if not instrument:
            continue

        event_id = _make_event_id(event)

        # Initialize state
        _event_state[event_id] = {
            "event": event,
            "instrument": instrument,
            "event_time": event_time,
            "pre_analysis": None,
            "scraped": None,
            "surprise": None,
        }

        cfg = NEWS_TRADING_CONFIG

        # Schedule T-2min: pre-analysis
        pre_time = event_time + timedelta(seconds=cfg["pre_analysis_offset_seconds"])
        if pre_time > now:
            _scheduler.add_job(
                _job_pre_analysis, "date", run_date=pre_time,
                args=[event_id], id=f"pre_{event_id}", replace_existing=True,
            )

        # Schedule T+30s: scrape actual
        scrape_time = event_time + timedelta(seconds=cfg["scrape_offset_seconds"])
        if scrape_time > now:
            _scheduler.add_job(
                _job_scrape_actual, "date", run_date=scrape_time,
                args=[event_id], id=f"scrape_{event_id}", replace_existing=True,
            )

        # Schedule T+2min: trade decision
        decision_time = event_time + timedelta(seconds=cfg["trade_decision_offset_seconds"])
        if decision_time > now:
            _scheduler.add_job(
                _job_trade_decision, "date", run_date=decision_time,
                args=[event_id], id=f"decision_{event_id}", replace_existing=True,
            )

        scheduled_count += 1
        log_to_firestore(
            f"[NewsScheduler] Scheduled {event['title']} ({event['country']}) "
            f"at {event_time.strftime('%H:%M UTC')} -> {instrument}",
            level="INFO"
        )

    log_to_firestore(
        f"[NewsScheduler] {scheduled_count} high-impact events scheduled for today",
        level="INFO"
    )


def start():
    """Start the news trading scheduler."""
    global _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")

    # Load today's events immediately
    load_and_schedule_today()

    # Refresh daily at 00:05 UTC
    _scheduler.add_job(
        load_and_schedule_today, "cron",
        hour=0, minute=5, id="daily_refresh", replace_existing=True,
    )

    _scheduler.start()
    log_to_firestore("[NewsScheduler] Background scheduler started", level="INFO")
