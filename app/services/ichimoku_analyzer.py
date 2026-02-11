# app/services/ichimoku_analyzer.py
import os
import json
from datetime import datetime, timezone
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def rule_based_filter(signal: dict) -> dict:
    """
    Filtre rule-based Ichimoku.
    signal contient: close, tenkan, kijun, ssa, ssb, chikou, chikou_ref_price, direction
    """
    d = signal["direction"]
    close = signal["close"]
    tenkan = signal["tenkan"]
    kijun = signal["kijun"]
    ssa = signal["ssa"]
    ssb = signal["ssb"]
    chikou = signal.get("chikou")
    chikou_ref = signal.get("chikou_ref_price")

    kumo_top = max(ssa, ssb)
    kumo_bottom = min(ssa, ssb)
    reasons = []

    if d == "LONG":
        if close > kumo_top:
            reasons.append("Prix au-dessus du Kumo")
        else:
            return {"valid": False, "direction": d, "reasons": ["Prix pas au-dessus du Kumo"]}
        if tenkan > kijun:
            reasons.append("Tenkan > Kijun (momentum haussier)")
        else:
            return {"valid": False, "direction": d, "reasons": ["Tenkan <= Kijun"]}
        if chikou and chikou_ref and chikou > chikou_ref:
            reasons.append("Chikou confirme (au-dessus du prix passe)")
    elif d == "SHORT":
        if close < kumo_bottom:
            reasons.append("Prix en-dessous du Kumo")
        else:
            return {"valid": False, "direction": d, "reasons": ["Prix pas en-dessous du Kumo"]}
        if tenkan < kijun:
            reasons.append("Tenkan < Kijun (momentum baissier)")
        else:
            return {"valid": False, "direction": d, "reasons": ["Tenkan >= Kijun"]}
        if chikou and chikou_ref and chikou < chikou_ref:
            reasons.append("Chikou confirme (en-dessous du prix passe)")

    return {"valid": True, "direction": d, "reasons": reasons}


def _parse_gpt_json(text: str) -> dict:
    """Parse JSON from GPT response, handling markdown code blocks."""
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return None


def _enrich_events_with_actuals(all_events: list) -> str:
    """Build calendar text with actual values for already-released events."""
    from app.services.news_data_service import fetch_actual_value

    now = datetime.now(timezone.utc)
    lines = []

    for e in all_events:
        parts = (
            f"- {e['time']} {e['country']}: {e['title']} "
            f"(impact: {e['impact']}, forecast: {e.get('forecast','N/A')}, "
            f"previous: {e.get('previous','N/A')}"
        )

        # For past high-impact events, try to fetch actual value
        dt_str = e.get("datetime_utc")
        if dt_str and e.get("impact") == "High":
            try:
                event_dt = datetime.fromisoformat(dt_str)
                if event_dt < now:
                    event_date = event_dt.strftime("%Y-%m-%d")
                    result = fetch_actual_value(e["title"], e["country"], event_date)
                    if result.get("success") and result.get("actual_raw"):
                        parts += f", ACTUAL: {result['actual_raw']}"
            except Exception:
                pass

        parts += ")"
        lines.append(parts)

    return "\n".join(lines) or "Aucun evenement economique notable."


def gpt_macro_analysis(oanda_instrument: str, all_events: list) -> dict:
    """Etape 1 : analyse macro — quelle est la tendance attendue sur l'instrument
    en fonction de TOUTES les news economiques du jour ?
    Enrichit les events passes avec les valeurs actuelles (via Investing.com)."""

    base, quote = oanda_instrument.split("_")

    calendar_text = _enrich_events_with_actuals(all_events)

    prompt = f"""Tu es un analyste macro forex. Voici le calendrier economique complet du jour (toutes devises).
Les evenements deja publies incluent leur valeur ACTUAL.

{calendar_text}

En te basant sur ces evenements (en particulier les surprises entre ACTUAL et forecast), quelle est ta vision pour la paire {base}/{quote} aujourd'hui ?
- Quelles news publiees ont impacte {base} ou {quote} ? (comparer ACTUAL vs forecast)
- Quelles news a venir pourraient encore impacter {base} ou {quote} ?
- Quel est le biais directionnel resultant pour {base}/{quote} ?

Reponds en JSON strict:
{{"bias": "BULLISH" ou "BEARISH" ou "NEUTRAL", "confidence": 0-100, "analysis": "explication courte de ton raisonnement"}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=300,
    )
    text = response.choices[0].message.content.strip()
    parsed = _parse_gpt_json(text)
    if parsed:
        return parsed
    return {"bias": "NEUTRAL", "confidence": 0, "analysis": f"Parse error: {text[:100]}"}


