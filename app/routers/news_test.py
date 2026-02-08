# app/routers/news_test.py
from fastapi import APIRouter, Request
from datetime import datetime, timezone
from app.services.log_service import log_to_firestore
from app.services.news_data_service import fetch_actual_value, calculate_surprise, parse_numeric_value
from app.services.news_analyzer import pre_release_analysis, post_release_decision
from app.services.calendar_service import get_all_upcoming_events
from app.services import news_scheduler

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
    """List all currently scheduled news events and their state."""
    events = []
    for event_id, state in news_scheduler._event_state.items():
        events.append({
            "event_id": event_id,
            "title": state["event"]["title"],
            "country": state["event"]["country"],
            "instrument": state["instrument"],
            "event_time": state["event_time"].isoformat() if state.get("event_time") else None,
            "has_pre_analysis": state.get("pre_analysis") is not None,
            "has_surprise": state.get("surprise") is not None,
            "surprise": state.get("surprise"),
        })
    return {"scheduled_events": events, "count": len(events)}
