import os
import json
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
RISK_CHF = 200
MIN_DELAY_MINUTES = 5
SENTIMENT_THRESHOLD_LONG = 70
SENTIMENT_THRESHOLD_SHORT = 30

def get_candle_history(db, day):
    candles = db.collection("ohlc_1m").where("day", "==", day).order_by("utc_time").stream()
    return [
        {"t": c.to_dict()["utc_time"], "o": c.to_dict()["o"], "h": c.to_dict()["h"],
         "l": c.to_dict()["l"], "c": c.to_dict()["c"]}
        for c in candles
    ]

# ... imports inchangés ...

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # Heure NY
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 📊 Range d'ouverture
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        return
    range_data = range_doc.to_dict()
    high_15, low_15 = range_data["high"], range_data["low"]

    # 📰 Vérifie score news (seulement si > 60 ou < 40)
    score_docs = db.collection("news_sentiment_score").order_by("timestamp", direction="DESCENDING").limit(1).stream()
    score_doc = next(score_docs, None)
    if not score_doc:
        log_to_firestore(f"[{STRATEGY_KEY}] Pas de news sentiment dispo", level="NO_TRADING")
        return

    note = score_doc.to_dict().get("note", 50)
    if 40 <= note <= 60:
        log_to_firestore(f"[{STRATEGY_KEY}] Marché sans tendance claire (score news = {note}) → pas de traitement", level="NO_TRADING")
        return

    # 📈 Récupère jusqu’à 90 dernières bougies
    all_candles = get_candle_history(db, today)
    last_candles = all_candles[-90:]
    history_text = "\n".join([
        f"{c['t'][11:16]} - o:{c['o']:.2f} h:{c['h']:.2f} l:{c['l']:.2f} c:{c['c']:.2f}"
        for c in last_candles
    ])

    prompt = (
        f"Range d'ouverture (09:30–09:45 NY) : High = {high_15:.2f}, Low = {low_15:.2f}\n"
        f"Dernière bougie : o={candle['o']:.2f}, h={candle['h']:.2f}, l={candle['l']:.2f}, c={candle['c']:.2f}\n\n"
        "Analyse les bougies suivantes et détecte une opportunité de trade intraday si elle existe "
        "(breakout, fake breakout, range reversion, etc.).\n"
        "Tu peux proposer un trade `long`, `short`, ou aucun si le marché n’est pas clair.\n"
        "Conditions : ratio TP/SL ≥ 2, SL et TP logiques.\n\n"
        "Réponds uniquement avec ce JSON STRICT :\n"
        '{\n'
        '  "prendre_position": true ou false,\n'
        '  "direction": "long" ou "short",\n'
        '  "justification": "...",\n'
        '  "sl_ref": float,\n'
        '  "tp_ref": float\n'
        '}\n\n'
        "Voici les bougies :\n" + history_text
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu es un assistant de trading. Tu réponds uniquement avec un JSON valide. Aucune explication ni phrase hors JSON."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.3,
            max_tokens=500
        )

        gpt_reply = response.choices[0].message.content.strip()
        log_to_firestore(f"[{STRATEGY_KEY}] Réponse GPT : {gpt_reply}", level="GPT")

        json_match = re.search(r"{.*}", gpt_reply, re.DOTALL)
        if not json_match:
            log_to_firestore(f"[{STRATEGY_KEY}] JSON non trouvé dans la réponse GPT", level="ERROR")
            return

        decision = json.loads(json_match.group())
        if not decision.get("prendre_position"):
            log_to_firestore(f"[{STRATEGY_KEY}] Aucune prise de position suggérée", level="NO_TRADING")
            return

        direction = decision["direction"].upper()
        sl_ref = float(decision["sl_ref"])
        tp_ref = float(decision["tp_ref"])
        justification = decision.get("justification", "")

        entry = get_entry_price()
        spread_factor = entry / candle["c"]
        sl_price = sl_ref * spread_factor
        tp_price = tp_ref * spread_factor

        sl_dist = abs(entry - sl_price)
        tp_dist = abs(tp_price - entry)
        if tp_dist < 2 * sl_dist:
            log_to_firestore(f"[{STRATEGY_KEY}] Ratio TP/SL insuffisant", level="ERROR")
            return

        risk_per_unit = sl_dist
        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            log_to_firestore(f"[{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
            return

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
                log_to_firestore(f"[{STRATEGY_KEY}] Même direction que précédent, ignoré", level="NO_TRADING")
                return

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

