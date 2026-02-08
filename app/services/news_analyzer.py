# app/services/news_analyzer.py
import os
import json
from openai import OpenAI
from app.services.log_service import log_to_firestore

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _parse_gpt_json(text: str) -> dict | None:
    """Parse JSON from GPT response, handling markdown code blocks."""
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return None


def pre_release_analysis(event: dict, instrument: str, all_events: list) -> dict:
    """
    GPT pre-analysis at T-2min before economic release.

    Args:
        event: {title, country, forecast, previous, time, impact}
        instrument: e.g. "EUR_USD"
        all_events: full day's economic calendar

    Returns:
        {bias, confidence, analysis, expected_direction_if_beat, expected_direction_if_miss}
    """
    base, quote = instrument.split("_")

    calendar_text = "\n".join(
        f"- {e['time']} {e['country']}: {e['title']} (impact: {e['impact']}, "
        f"forecast: {e.get('forecast', 'N/A')}, previous: {e.get('previous', 'N/A')})"
        for e in all_events
    ) or "Aucun autre evenement."

    prompt = f"""Tu es un analyste macro forex specialise dans le news trading.

Evenement imminent:
- Titre: {event['title']}
- Pays/Devise: {event['country']}
- Forecast (consensus): {event.get('forecast', 'N/A')}
- Previous: {event.get('previous', 'N/A')}
- Heure: {event.get('time', 'N/A')}

Calendrier complet du jour:
{calendar_text}

Paire analysee: {base}/{quote}

Analyse:
1. Quel est le contexte macro actuel pour {event['country']} ?
2. Si le chiffre sort AU-DESSUS du forecast, quel impact sur {base}/{quote} ? (BULLISH ou BEARISH)
3. Si le chiffre sort EN-DESSOUS du forecast, quel impact sur {base}/{quote} ? (BULLISH ou BEARISH)
4. Quel est ton biais pre-release pour cette paire ?

Reponds en JSON strict:
{{"bias": "BULLISH" ou "BEARISH" ou "NEUTRAL", "confidence": 0-100, "analysis": "explication courte", "expected_direction_if_beat": "BULLISH" ou "BEARISH", "expected_direction_if_miss": "BULLISH" ou "BEARISH"}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        text = response.choices[0].message.content.strip()
        parsed = _parse_gpt_json(text)
        if parsed:
            log_to_firestore(
                f"[NewsAnalyzer] Pre-analysis for {event['title']} on {instrument}: "
                f"bias={parsed.get('bias')}, confidence={parsed.get('confidence')}",
                level="INFO"
            )
            return parsed
        return {
            "bias": "NEUTRAL", "confidence": 0,
            "analysis": f"Parse error: {text[:100]}",
            "expected_direction_if_beat": "BULLISH",
            "expected_direction_if_miss": "BEARISH",
        }
    except Exception as e:
        log_to_firestore(f"[NewsAnalyzer] GPT error: {e}", level="ERROR")
        return {
            "bias": "NEUTRAL", "confidence": 0,
            "analysis": f"GPT call failed: {e}",
            "expected_direction_if_beat": "BULLISH",
            "expected_direction_if_miss": "BEARISH",
        }


def post_release_decision(event: dict, surprise: dict, pre_analysis: dict, instrument: str) -> dict:
    """
    Rule-based trade decision at T+2min after economic release.

    Returns:
        {action: "TRADE" or "SKIP", reason, confidence}
    """
    magnitude = surprise.get("magnitude", "UNKNOWN")
    direction = surprise.get("direction", "UNKNOWN")
    gpt_bias = pre_analysis.get("bias", "NEUTRAL")

    # No data scraped — skip
    if direction == "UNKNOWN" or magnitude == "UNKNOWN":
        return {"action": "SKIP", "reason": "Actual value not available", "confidence": 0}

    # Inline with forecast — no trade
    if direction == "INLINE":
        return {"action": "SKIP", "reason": "Actual inline with forecast", "confidence": 0}

    # Small surprise — never trade
    if magnitude == "SMALL":
        return {"action": "SKIP", "reason": f"Surprise too small ({magnitude})", "confidence": 20}

    # Determine if surprise aligns with GPT bias
    surprise_bullish_for_currency = direction == "ABOVE"  # beat = bullish for that currency

    # Check inverse events (higher = bearish)
    if _is_inverse_event(event["title"]):
        surprise_bullish_for_currency = not surprise_bullish_for_currency

    # Map currency impact to instrument direction
    base, quote = instrument.split("_")
    event_currency = event["country"].upper()

    if event_currency == base:
        instrument_direction = "BULLISH" if surprise_bullish_for_currency else "BEARISH"
    elif event_currency == quote:
        instrument_direction = "BEARISH" if surprise_bullish_for_currency else "BULLISH"
    else:
        return {"action": "SKIP", "reason": "Event currency not in instrument pair", "confidence": 0}

    # Check alignment with GPT bias
    bias_aligned = (
        gpt_bias == "NEUTRAL" or
        gpt_bias == instrument_direction
    )
    bias_contrary = (
        gpt_bias != "NEUTRAL" and
        gpt_bias != instrument_direction
    )

    # Decision matrix
    if magnitude == "LARGE" and bias_aligned:
        return {
            "action": "TRADE",
            "reason": f"Large surprise ({direction}) aligned with GPT bias ({gpt_bias})",
            "confidence": 85,
            "instrument_direction": instrument_direction,
        }

    if magnitude == "LARGE" and bias_contrary:
        return {
            "action": "SKIP",
            "reason": f"Large surprise but GPT bias contrary ({gpt_bias} vs {instrument_direction})",
            "confidence": 40,
        }

    if magnitude == "MEDIUM" and bias_aligned:
        return {
            "action": "TRADE",
            "reason": f"Medium surprise ({direction}) aligned with GPT bias ({gpt_bias})",
            "confidence": 65,
            "instrument_direction": instrument_direction,
        }

    if magnitude == "MEDIUM" and bias_contrary:
        return {
            "action": "SKIP",
            "reason": f"Medium surprise but GPT bias contrary ({gpt_bias} vs {instrument_direction})",
            "confidence": 30,
        }

    return {"action": "SKIP", "reason": "No clear signal", "confidence": 10}


def _is_inverse_event(title: str) -> bool:
    """Return True if higher actual = bearish for the currency (e.g. unemployment)."""
    inverse_keywords = [
        "unemployment rate",
        "jobless claims",
        "initial claims",
        "continuing claims",
    ]
    title_lower = title.lower()
    return any(kw in title_lower for kw in inverse_keywords)
