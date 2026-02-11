# app/services/news_scheduler.py
import re
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.services.calendar_service import _fetch_calendar, _parse_event_datetime
from app.services.news_data_service import fetch_actual_value, calculate_surprise, parse_numeric_value, get_day_cache
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
    "scrape_offset_seconds": 30,           # T+30s (Investing.com updates in ~1-2min)
    "trade_decision_offset_seconds": 120,  # T+2min (retry scrape if T+30s was too early)
}

# Which instruments to trade for each currency
CURRENCY_INSTRUMENTS = {
    "USD": ["USD_CHF", "EUR_USD", "USD_JPY"],
    "EUR": ["EUR_USD", "EUR_GBP"],
    "GBP": ["GBP_USD", "EUR_GBP"],
    "JPY": ["USD_JPY", "EUR_JPY"],
    "CHF": ["USD_CHF"],
    "AUD": ["AUD_USD"],
    "NZD": ["NZD_USD"],
    "CAD": ["USD_CAD"],
}

# In-memory state for each group being tracked
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


def _job_pre_analysis(group_id: str):
    """T-2min job: run GPT pre-release analysis for the primary event in a group."""
    state = _event_state.get(group_id)
    if not state:
        return

    # Use the first event as primary for GPT analysis
    primary = state["events"][0]
    event = primary["event"]
    instrument = state["instrument"]
    event_titles = [e["event"]["title"] for e in state["events"]]

    log_to_firestore(
        f"[NewsScheduler] T-2min: pre-analysis for {group_id} "
        f"({', '.join(event_titles)}) on {instrument}",
        level="INFO"
    )

    try:
        all_events = get_all_upcoming_events()
        analysis = pre_release_analysis(event, instrument, all_events)
        state["pre_analysis"] = analysis

        db = get_firestore()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{group_id}").set({
                "group_id": group_id,
                "events": event_titles,
                "instrument": instrument,
                "phase": "pre_analysis",
                "pre_analysis": analysis,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)

    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Pre-analysis error for {group_id}: {e}", level="ERROR")
        state["pre_analysis"] = {
            "bias": "NEUTRAL", "confidence": 0, "analysis": f"Error: {e}",
            "expected_direction_if_beat": "BULLISH",
            "expected_direction_if_miss": "BEARISH",
        }


def _job_scrape_actual(group_id: str):
    """T+30s job: scrape actual values from Investing.com for all events in the group."""
    state = _event_state.get(group_id)
    if not state:
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_titles = [e["event"]["title"] for e in state["events"]]

    log_to_firestore(
        f"[NewsScheduler] T+30s: scraping actuals for {group_id} "
        f"({len(state['events'])} events: {', '.join(event_titles)})",
        level="INFO"
    )

    for entry in state["events"]:
        event = entry["event"]
        event_id = entry["event_id"]

        try:
            # All calls share the day-level TE API cache — no redundant HTTP requests
            scraped = fetch_actual_value(event["title"], event["country"], today)
            entry["scraped"] = scraped

            if scraped["success"] and scraped["actual"] is not None:
                forecast = scraped["forecast"]
                if forecast is None:
                    forecast = parse_numeric_value(event.get("forecast", ""))
                    scraped["forecast"] = forecast

                surprise = calculate_surprise(scraped["actual"], forecast)
                entry["surprise"] = surprise

                log_to_firestore(
                    f"[NewsScheduler] {event['title']}: actual={scraped['actual']}, "
                    f"forecast={forecast}, surprise={surprise['direction']} ({surprise['magnitude']})",
                    level="INFO"
                )
            else:
                entry["surprise"] = {"direction": "UNKNOWN", "magnitude": "UNKNOWN", "surprise": None}
                log_to_firestore(
                    f"[NewsScheduler] No actual value for {event['title']}",
                    level="INFO"
                )

        except Exception as e:
            log_to_firestore(f"[NewsScheduler] Scrape error for {event_id}: {e}", level="ERROR")
            entry["surprise"] = {"direction": "UNKNOWN", "magnitude": "UNKNOWN", "surprise": None}

    # Find event with strongest surprise (highest pct_deviation)
    best_idx = None
    best_pct = -1
    for i, entry in enumerate(state["events"]):
        s = entry.get("surprise")
        if s and s.get("surprise") is not None:
            pct = abs(s.get("pct_deviation") or 0)
            if pct > best_pct:
                best_pct = pct
                best_idx = i

    state["best_event_idx"] = best_idx

    if best_idx is not None:
        best = state["events"][best_idx]
        log_to_firestore(
            f"[NewsScheduler] Best surprise in {group_id}: {best['event']['title']} "
            f"({best['surprise']['direction']}, {best['surprise']['magnitude']}, {best_pct:.1f}%)",
            level="INFO"
        )

    # Log to Firestore
    try:
        db = get_firestore()
        scrape_summary = {
            entry["event_id"]: {
                "title": entry["event"]["title"],
                "surprise": entry.get("surprise"),
            }
            for entry in state["events"]
        }
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{group_id}").set({
                "phase": "scraped",
                "scrape_results": scrape_summary,
                "best_event": state["events"][best_idx]["event"]["title"] if best_idx is not None else None,
                "scrape_timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)
    except Exception:
        pass


def _job_trade_decision(group_id: str):
    """T+2min job: make one trade decision using the strongest surprise in the group (retries scrape if needed)."""
    state = _event_state.get(group_id)
    if not state:
        return

    instrument = state["instrument"]
    pre_analysis = state.get("pre_analysis") or {"bias": "NEUTRAL", "confidence": 0}
    best_idx = state.get("best_event_idx")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Retry scrape if initial attempt failed (T+30s may have been too early)
    if best_idx is None:
        log_to_firestore(
            f"[NewsScheduler] No surprise data at decision time for {group_id}, retrying scrape...",
            level="INFO"
        )
        # Invalidate day cache to force fresh API call
        if today in get_day_cache():
            del get_day_cache()[today]
        _job_scrape_actual(group_id)
        best_idx = state.get("best_event_idx")

    if best_idx is None:
        log_to_firestore(
            f"[NewsScheduler] No valid surprise data for {group_id} after retry, skipping",
            level="INFO"
        )
        _log_decision_to_firestore(group_id, today, "SKIP", "No valid surprise data (after retry)", state)
        return

    best = state["events"][best_idx]
    event = best["event"]
    event_id = best["event_id"]
    surprise = best.get("surprise", {"direction": "UNKNOWN", "magnitude": "UNKNOWN"})

    log_to_firestore(
        f"[NewsScheduler] T+2min: trade decision for {group_id}, "
        f"best event: {event['title']} on {instrument}",
        level="INFO"
    )

    # One GPT decision for the whole group
    decision = post_release_decision(event, surprise, pre_analysis, instrument)

    log_to_firestore(
        f"[NewsScheduler] Decision for {group_id}: {decision['action']} - {decision['reason']}",
        level="INFO"
    )

    _log_decision_to_firestore(group_id, today, decision["action"], decision["reason"], state)

    if decision["action"] != "TRADE":
        return

    # One trade execution for the whole group
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


def _log_decision_to_firestore(group_id: str, today: str, action: str, reason: str, state: dict):
    """Log the trade decision to Firestore for analysis."""
    try:
        db = get_firestore()
        db.collection("strategies").document("news_trading") \
            .collection("events").document(f"{today}_{group_id}").set({
                "phase": "decision",
                "decision_action": action,
                "decision_reason": reason,
                "decision_timestamp": datetime.now(timezone.utc).isoformat(),
            }, merge=True)
    except Exception:
        pass


def load_and_schedule_today():
    """Load today's high-impact events, group by (time, instrument), schedule 3 jobs per group."""
    global _event_state

    try:
        events = _fetch_calendar()
    except Exception as e:
        log_to_firestore(f"[NewsScheduler] Failed to fetch calendar: {e}", level="ERROR")
        return

    now = datetime.now(timezone.utc)

    # Group events by (event_time, instrument)
    groups = {}

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
        group_key = f"{instrument}_{event_time.strftime('%Y%m%d_%H%M')}"

        if group_key not in groups:
            groups[group_key] = {
                "events": [],
                "instrument": instrument,
                "event_time": event_time,
            }

        groups[group_key]["events"].append({
            "event": event,
            "event_id": event_id,
            "scraped": None,
            "surprise": None,
        })

    # Schedule jobs for each group
    _event_state = {}
    scheduled_count = 0

    for group_id, group in groups.items():
        event_time = group["event_time"]
        instrument = group["instrument"]

        _event_state[group_id] = {
            **group,
            "pre_analysis": None,
            "best_event_idx": None,
        }

        cfg = NEWS_TRADING_CONFIG

        # Schedule T-2min: pre-analysis (1 per group)
        pre_time = event_time + timedelta(seconds=cfg["pre_analysis_offset_seconds"])
        if pre_time > now:
            _scheduler.add_job(
                _job_pre_analysis, "date", run_date=pre_time,
                args=[group_id], id=f"pre_{group_id}", replace_existing=True,
            )

        # Schedule T+30s: scrape actuals from Investing.com (1 per group)
        scrape_time = event_time + timedelta(seconds=cfg["scrape_offset_seconds"])
        if scrape_time > now:
            _scheduler.add_job(
                _job_scrape_actual, "date", run_date=scrape_time,
                args=[group_id], id=f"scrape_{group_id}", replace_existing=True,
            )

        # Schedule T+2min: trade decision with retry (1 per group)
        decision_time = event_time + timedelta(seconds=cfg["trade_decision_offset_seconds"])
        if decision_time > now:
            _scheduler.add_job(
                _job_trade_decision, "date", run_date=decision_time,
                args=[group_id], id=f"decision_{group_id}", replace_existing=True,
            )

        event_titles = [e["event"]["title"] for e in group["events"]]
        scheduled_count += len(group["events"])
        log_to_firestore(
            f"[NewsScheduler] Scheduled {group_id} ({len(group['events'])} events: "
            f"{', '.join(event_titles)}) at {event_time.strftime('%H:%M UTC')}",
            level="INFO"
        )

    log_to_firestore(
        f"[NewsScheduler] {scheduled_count} events in {len(groups)} groups scheduled for today",
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
