import os
import json
import html
import re
import uuid
from datetime import datetime, timezone, timedelta
import pytz
from openai import OpenAI

from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price,
    compute_position_size,
    execute_trade
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

STRATEGY_KEY = "gpt_trader"
RISK_CHF = 50
MIN_DELAY_MINUTES = 5  # délai minimum entre deux trades

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

    # ⏱️ Heure NY
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # ⚙️ Activation stratégie
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Récupération du range d'ouverture
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

    # 📈 Historique complet depuis 09:30 NY
    history = get_candle_history(db, today)
    history_text = "\n".join([f"{c['t']} - o:{c['o']} h:{c['h']} l:{c['l']} c:{c['c']}" for c in history])

    # 🧠 Construction prompt GPT
    prompt = (
        f"Bougies du jour (UTC) depuis 09:30 NY jusqu'à maintenant :\n{history_text}\n\n"
        f"Range d'ouverture (09:30–09:45 NY) : High = {high_15}, Low = {low_15}\n"
        f"Dernière bougie : o={candle['o']}, h={candle['h']}, l={candle['l']}, c={candle['c']}\n\n"
        f"News importantes du jour :\n{safe_news}\n\n"
        "Ta mission : détecter une opportunité de trade intraday (breakout, fake breakout, range reversion, etc.).\n"
        "Conditions à respecter :\n"
        "- Le TP doit être au moins 2x plus éloigné que le SL (ratio TP/SL ≥ 2)\n"
        "- Les niveaux SL et TP doivent être basés sur des zones logiques (support, résistance, excès récents...)\n"
        "- Si aucune opportunité claire, ne pas proposer de trade\n\n"
        "Réponds uniquement avec ce JSON :\n"
        '{\n'
        '  "prendre_position": true ou false,\n'
        '  "direction": "long" ou "short",\n'
        '  "justification": "...",\n'
        '  "sl_ref": float,\n'
        '  "tp_ref": float\n'
        '}'
    )

    try:
        # 🧠 Appel GPT
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu analyses bougies et news pour détecter des opportunités de trade intraday SPX."},
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
        spread_factor = entry / candle["c"]

        sl_price = sl_ref * spread_factor
        tp_price = tp_ref * spread_factor

        # 🔁 Ratio vérification
        sl_dist = abs(entry - sl_price)
        tp_dist = abs(tp_price - entry)
        if tp_dist < 2 * sl_dist:
            log_to_firestore(f"[{STRATEGY_KEY}] Ratio TP/SL insuffisant", level="ERROR")
            return

        # 📐 Position sizing
        risk_per_unit = abs(entry - sl_price)
        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            log_to_firestore(f"[{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
            return

        # 🔁 Anti-replication (temps + direction)
        trades_ref = db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).collection("executions")
        trades = list(trades_ref.stream())
        if trades:
            latest = max(trades, key=lambda t: t.to_dict().get("timestamp", ""))
            last = latest.to_dict()
            last_time = datetime.fromisoformat(last["timestamp"])
            if (datetime.now() - last_time) < timedelta(minutes=MIN_DELAY_MINUTES):
                log_to_firestore(f"[{STRATEGY_KEY}] Trade trop récent", level="INFO")
                return
            if last.get("direction") == direction:
                log_to_firestore(f"[{STRATEGY_KEY}] Même direction que le trade précédent, ignoré", level="NO_TRADING")
                return

        # ✅ Exécution
        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"[{STRATEGY_KEY}] Trade {direction} exécuté ({executed_units} unités)", level="TRADING")

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
        log_to_firestore(f"[{STRATEGY_KEY}] Erreur GPT ou exécution : {e}", level="ERROR")
