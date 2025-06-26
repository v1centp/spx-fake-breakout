import os
import json
import html
import re
from datetime import datetime, timezone
import pytz
from openai import OpenAI
import uuid

from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price,
    convert_distance_to_price,
    compute_position_size,
    execute_trade
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
STRATEGY_KEY = "gpt_trader"
RISK_CHF = 50

def get_candle_history(db, day):
    candles = db.collection("ohlc_1m").where("day", "==", day).order_by("utc_time").stream()
    return [
        {"t": c.to_dict()["utc_time"], "o": c.to_dict()["o"], "h": c.to_dict()["h"],
         "l": c.to_dict()["l"], "c": c.to_dict()["c"]}
        for c in candles
    ]

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # ⏱️ Filtre horaire (09:45 – 11:30 NY)
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ✅ Stratégie activée ?
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Range d'ouverture
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        return
    range_data = range_doc.to_dict()
    high_15, low_15 = range_data["high"], range_data["low"]

    # 📰 News du jour
    news_docs = db.collection("all_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("fetched_at", ">=", f"{today}T00:00:00Z") \
        .stream()
    news_summary = "\n".join([n.to_dict().get("summary", "") for n in news_docs])
    safe_news = html.escape(news_summary).replace('"', "'")

    # 📈 Historique des bougies
    history = get_candle_history(db, today)
    history_text = "\n".join([f"{c['t']} - o:{c['o']} h:{c['h']} l:{c['l']} c:{c['c']}" for c in history[-30:]])

    # 🤖 Prompt GPT
    prompt = (
        f"Historique des 30 dernières bougies (UTC) :\n{history_text}\n\n"
        f"Range d'ouverture (09:30–09:45 NY) : High = {high_15}, Low = {low_15}\n"
        f"Dernière bougie : o={candle['o']}, h={candle['h']}, l={candle['l']}, c={candle['c']}\n"
        f"News du jour :\n{safe_news}\n\n"
        "Dois-je entrer un trade ? Réponds uniquement avec ce JSON :\n"
        '{\n'
        '  "prendre_position": true ou false,\n'
        '  "direction": "long" ou "short",\n'
        '  "justification": "...",\n'
        '  "sl_ref": float,  // niveau technique de stop loss (ex: 6098.0)\n'
        '  "tp_ref": float   // niveau technique de take profit (ex: 6130.0)\n'
        '}'
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu analyses bougies et news pour détecter des breakout ou fake breakout et prendre un trade intraday."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.3
        )
        gpt_reply = response.choices[0].message.content.strip()
        log_to_firestore(f"[{STRATEGY_KEY}] Réponse GPT : {gpt_reply}", level="GPT")

        json_match = re.search(r"{.*}", gpt_reply, re.DOTALL)
        if not json_match:
            log_to_firestore(f"[{STRATEGY_KEY}] JSON invalide", level="ERROR")
            return

        decision = json.loads(json_match.group())
        if not decision.get("prendre_position"):
            return

        direction = decision["direction"].upper()
        sl_ref = float(decision["sl_ref"])
        tp_ref = float(decision["tp_ref"])
        justification = decision.get("justification", "")

        entry = get_entry_price()

        # ⚠️ Validation du ratio (min 1:2 par ex.)
        sl_dist = abs(entry - sl_ref)
        tp_dist = abs(tp_ref - entry)
        if tp_dist < 2 * sl_dist:
            log_to_firestore(f"[{STRATEGY_KEY}] Ratio TP/SL trop faible", level="ERROR")
            return

        sl_price = convert_distance_to_price(entry, sl_ref)
        tp_price = convert_distance_to_price(entry, tp_ref)
        risk_per_unit = abs(entry - sl_price)

        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            log_to_firestore(f"[{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
            return

        # 🚫 Max 5 trades/jour pour cette stratégie
        trades_ref = db.collection("trading_days").document(today).collection("trades")
        trades_for_strategy = list(trades_ref.where("strategy", "==", STRATEGY_KEY).stream())
        if len(trades_for_strategy) >= 5:
            log_to_firestore(f"[{STRATEGY_KEY}] Déjà 5 trades exécutés", level="TRADING")
            return

        # ✅ Envoi ordre
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"[{STRATEGY_KEY}] Trade {direction} exécuté : {executed_units} unités", level="TRADING")

        trades_ref.document(str(uuid.uuid4())).set({
            "strategy": STRATEGY_KEY,
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed_units,
            "timestamp": datetime.now().isoformat(),
            "meta": {
                "justification": justification,
                "prendre_position": True,
                "sl_ref": sl_ref,
                "tp_ref": tp_ref
            }
        })

    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Erreur GPT : {e}", level="ERROR")
