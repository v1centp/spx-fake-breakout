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


def gpt_analysis(signal: dict, calendar_events: list) -> dict:
    calendar_text = "\n".join(
        f"- {e['time']} {e['country']}: {e['title']} (impact: {e['impact']})"
        for e in calendar_events
    ) or "Aucun evenement majeur proche."

    prompt = f"""Tu es un analyste forex specialise en Ichimoku Kinko Hyo sur timeframe H1.

Signal detecte:
- Instrument: {signal.get('instrument', 'N/A')}
- Direction: {signal['direction']}
- Prix actuel: {signal['close']}
- Tenkan-sen: {signal['tenkan']}
- Kijun-sen: {signal['kijun']}
- SSA (Senkou Span A): {signal['ssa']}
- SSB (Senkou Span B): {signal['ssb']}
- Chikou Span: {signal.get('chikou', 'N/A')}

Calendrier economique du jour:
{calendar_text}

Analyse ce signal et reponds en JSON strict:
{{"decision": "GO" ou "NO_GO", "confidence": 0-100, "reason": "explication courte"}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
    )
    text = response.choices[0].message.content.strip()
    try:
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return {"decision": "NO_GO", "confidence": 0, "reason": f"Parse error: {text[:100]}"}
