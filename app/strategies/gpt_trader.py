import os
import openai
from datetime import datetime, timezone
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

openai.api_key = os.getenv("OPENAI_API_KEY")
db = get_firestore()

STRATEGY_KEY = "gpt_trader"
RISK_CHF = 50

def get_today_candles(day_str):
    query = db.collection("ohlc_1m").where("day", "==", day_str).where("sym", "==", "I:SPX")
    return [doc.to_dict() for doc in query.stream()]

def process(candle):
    today = candle["day"]

    # 1. Vérifie présence du range
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        return
    range_data = range_doc.to_dict()
    if range_data.get("status") != "ready":
        return

    high_15m = range_data["high"]
    low_15m = range_data["low"]

    # 2. Bougies du jour
    candles = get_today_candles(today)

    # 3. News pertinentes
    news_docs = db.collection("polygon_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("published_utc", ">=", f"{today}T00:00:00Z") \
        .stream()
    news_summary = "\n".join([doc.to_dict().get("summary", "") for doc in news_docs])

    # 4. Appel GPT
    prompt = f"""
Tu es un trader intraday. Voici les infos disponibles :

Range des 15 premières minutes : High = {high_15m}, Low = {low_15m}
Dernière bougie : o={candle['o']}, h={candle['h']}, l={candle['l']}, c={candle['c']}
News importantes du jour :\n{news_summary}

Analyse la situation actuelle. S'il y a un signal de BREAKOUT ou de FAKE BREAKOUT clair, indique s'il faut entrer en position.
Réponds en JSON :
- "direction" : "long" ou "short"
- "justification" : string
- "risk_level" : float entre 0 et 1
- "type" : "breakout" ou "fake_breakout"
"""

    try:
        res = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu es un trader intraday expérimenté."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.3
        )

        decision = eval(res.choices[0].message.content.strip())
        risk_level = decision.get("risk_level", 0)
        if risk_level < 0.5:
            return  # Trop faible

        direction = decision["direction"].upper()
        entry = get_entry_price()
        log_to_firestore(f"[{STRATEGY_KEY}] 📊 Entry price from OANDA: {entry}", level="TRADING")

        sl_level = candle["l"] if direction == "LONG" else candle["h"]
        sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_level, direction)
        if risk_per_unit == 0:
            return

        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            return

        # ✅ Exécution
        execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] {decision.get('type')} {direction} → {decision.get('justification')}", level="TRADING")

        # 📝 Enregistrement
        db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).set({
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": units,
            "type": decision.get("type"),
            "justification": decision.get("justification"),
            "risk_level": risk_level,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Erreur GPT : {e}", level="ERROR")
