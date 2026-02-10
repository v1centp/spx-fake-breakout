# app/routers/news_test.py
from fastapi import APIRouter, Request
from datetime import datetime, timezone
from app.services.log_service import log_to_firestore
from app.services.firebase import get_firestore
from app.services.news_data_service import fetch_actual_value, calculate_surprise, parse_numeric_value
from app.services.news_analyzer import pre_release_analysis, post_release_decision
from app.services.calendar_service import _fetch_calendar, _parse_event_datetime, get_all_upcoming_events
from app.services import news_scheduler
from app.services.news_scheduler import CURRENCY_INSTRUMENTS

router = APIRouter()


@router.post("/news/test-run")
async def test_news_pipeline(request: Request):
    """
    Manually trigger the full news trading pipeline for testing.

    Body (all optional):
    {
        "event_title": "Nonfarm Payrolls",
        "country": "USD",
        "instrument": "USD_CHF",
        "forecast": "180K",
        "previous": "223K",
        "mock_actual": "263K",    // if set, skip scraping and use this value
        "dry_run": true           // if true, do NOT execute the trade
    }
    """
    body = await request.json()

    event_title = body.get("event_title", "Nonfarm Payrolls")
    country = body.get("country", "USD")
    instrument = body.get("instrument", "USD_CHF")
    forecast_raw = body.get("forecast", "180K")
    previous_raw = body.get("previous", "223K")
    mock_actual_raw = body.get("mock_actual")
    dry_run = body.get("dry_run", True)

    event = {
        "title": event_title,
        "country": country,
        "time": datetime.now(timezone.utc).strftime("%I:%M%p"),
        "impact": "High",
        "forecast": forecast_raw,
        "previous": previous_raw,
    }

    result = {"steps": []}

    # Step 1: GPT pre-analysis
    all_events = get_all_upcoming_events()
    pre_analysis = pre_release_analysis(event, instrument, all_events)
    result["steps"].append({
        "step": "pre_analysis",
        "result": pre_analysis,
    })

    # Step 2: Scrape or mock actual value
    if mock_actual_raw:
        actual = parse_numeric_value(mock_actual_raw)
        forecast = parse_numeric_value(forecast_raw)
        scraped = {
            "actual": actual,
            "forecast": forecast,
            "previous": parse_numeric_value(previous_raw),
            "actual_raw": mock_actual_raw,
            "forecast_raw": forecast_raw,
            "success": actual is not None,
        }
    else:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        scraped = fetch_actual_value(event_title, country, today)
        actual = scraped.get("actual")
        forecast = scraped.get("forecast") or parse_numeric_value(forecast_raw)

    result["steps"].append({
        "step": "scrape_actual",
        "result": scraped,
    })

    # Step 3: Calculate surprise
    surprise = calculate_surprise(actual, forecast)
    result["steps"].append({
        "step": "calculate_surprise",
        "result": surprise,
    })

    # Step 4: Post-release decision
    decision = post_release_decision(event, surprise, pre_analysis, instrument)
    result["steps"].append({
        "step": "trade_decision",
        "result": decision,
    })

    # Step 5: Trade direction
    from app.strategies.news_trading_strategy import _determine_trade_direction
    trade_dir = _determine_trade_direction(event, surprise, instrument)
    result["steps"].append({
        "step": "trade_direction",
        "result": {"direction": trade_dir},
    })

    # Step 6: Execute (only if not dry_run and decision is TRADE)
    if not dry_run and decision["action"] == "TRADE":
        from app.strategies.news_trading_strategy import execute_news_trade
        import re
        event_id = re.sub(r"[^a-zA-Z0-9]", "_", event_title)[:30]
        trade_result = execute_news_trade(
            event=event,
            event_id=event_id,
            instrument=instrument,
            pre_analysis=pre_analysis,
            surprise=surprise,
            decision=decision,
        )
        result["steps"].append({
            "step": "execute_trade",
            "result": trade_result,
        })
    else:
        result["steps"].append({
            "step": "execute_trade",
            "result": {"skipped": True, "dry_run": dry_run, "decision": decision["action"]},
        })

    result["summary"] = {
        "event": event_title,
        "instrument": instrument,
        "gpt_bias": pre_analysis.get("bias"),
        "surprise_direction": surprise.get("direction"),
        "surprise_magnitude": surprise.get("magnitude"),
        "decision": decision["action"],
        "trade_direction": trade_dir,
        "dry_run": dry_run,
    }

    return result


@router.get("/news/scheduled")
def get_scheduled_events():
    """List all currently scheduled news event groups and their state."""
    groups = []
    for group_id, state in news_scheduler._event_state.items():
        events_info = []
        for entry in state.get("events", []):
            events_info.append({
                "event_id": entry["event_id"],
                "title": entry["event"]["title"],
                "country": entry["event"]["country"],
                "surprise": entry.get("surprise"),
            })
        groups.append({
            "group_id": group_id,
            "instrument": state["instrument"],
            "event_time": state["event_time"].isoformat() if state.get("event_time") else None,
            "events": events_info,
            "event_count": len(events_info),
            "has_pre_analysis": state.get("pre_analysis") is not None,
            "best_event_idx": state.get("best_event_idx"),
        })
    return {"groups": groups, "count": len(groups)}


@router.get("/news/history")
def get_news_history():
    """Return past news events with their GPT analysis and trade decisions."""
    try:
        db = get_firestore()
        docs = db.collection("strategies").document("news_trading") \
            .collection("events").order_by("timestamp", direction="DESCENDING").limit(50).stream()

        events = []
        for doc in docs:
            data = doc.to_dict()
            pre = data.get("pre_analysis") or {}
            scrape = data.get("scrape_results") or {}

            # Build surprise summary from scrape_results
            surprises = []
            for eid, info in scrape.items():
                s = info.get("surprise") or {}
                surprises.append({
                    "title": info.get("title", eid),
                    "direction": s.get("direction"),
                    "magnitude": s.get("magnitude"),
                    "pct_deviation": s.get("pct_deviation"),
                })

            events.append({
                "id": doc.id,
                "group_id": data.get("group_id"),
                "instrument": data.get("instrument"),
                "event_titles": data.get("events", []),
                "timestamp": data.get("timestamp"),
                "phase": data.get("phase"),
                "gpt_bias": pre.get("bias"),
                "gpt_confidence": pre.get("confidence"),
                "gpt_analysis": pre.get("analysis"),
                "surprises": surprises,
                "best_event": data.get("best_event"),
                "decision_action": data.get("decision_action"),
                "decision_reason": data.get("decision_reason"),
                "decision_timestamp": data.get("decision_timestamp"),
            })

        return {"events": events}
    except Exception as e:
        return {"events": [], "error": str(e)}


@router.get("/news/calendar")
def get_news_calendar():
    """Return all upcoming high-impact events from ForexFactory for the week."""
    try:
        events = _fetch_calendar()
    except Exception:
        return {"events": [], "error": "Failed to fetch calendar"}

    now = datetime.now(timezone.utc)
    result = []

    for ev in events:
        event_time = _parse_event_datetime(ev)
        if not event_time:
            continue

        # Only future events
        if event_time < now:
            continue

        country = ev["country"].upper()
        instruments = CURRENCY_INSTRUMENTS.get(country, [])

        # Check if this event is currently scheduled in the news_scheduler
        scheduled = False
        for state in news_scheduler._event_state.values():
            for entry in state.get("events", []):
                if entry["event"]["title"] == ev["title"] and entry["event"]["country"] == ev["country"]:
                    scheduled = True
                    break
            if scheduled:
                break

        result.append({
            "title": ev["title"],
            "country": country,
            "impact": ev["impact"],
            "datetime_utc": event_time.isoformat(),
            "forecast": ev.get("forecast", ""),
            "previous": ev.get("previous", ""),
            "instruments": instruments,
            "scheduled": scheduled,
        })

    # Sort by time
    result.sort(key=lambda x: x["datetime_utc"])
    return {"events": result, "count": len(result)}
