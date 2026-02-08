# app/services/ichimoku_analyzer.py
import os
import json
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


def gpt_macro_analysis(oanda_instrument: str, all_events: list) -> dict:
    """Etape 1 : analyse macro — quelle est la tendance attendue sur l'instrument
    en fonction de TOUTES les news economiques du jour ?"""

    base, quote = oanda_instrument.split("_")

    calendar_text = "\n".join(
        f"- {e['time']} {e['country']}: {e['title']} (impact: {e['impact']}, forecast: {e.get('forecast','N/A')}, previous: {e.get('previous','N/A')})"
        for e in all_events
    ) or "Aucun evenement economique notable."

    prompt = f"""Tu es un analyste macro forex. Voici le calendrier economique complet du jour (toutes devises):

{calendar_text}

En te basant sur ces evenements, quelle est ta vision pour la paire {base}/{quote} aujourd'hui ?
- Quelles news pourraient impacter {base} (haussier ou baissier) ?
- Quelles news pourraient impacter {quote} (haussier ou baissier) ?
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


