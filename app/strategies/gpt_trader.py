import os
from datetime import datetime, timezone
import pytz
from openai import OpenAI

from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.services.shared_strategy_tools import (
    get_entry_price,
    calculate_sl_tp,
    compute_position_size,
    execute_trade
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

STRATEGY_KEY = "gpt_trader"
RISK_CHF = 50

def process(candle):
    db = get_firestore()
    today = candle["day"]

    # Vérifie plage horaire (entre 09:45 et 11:30 NY)
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        return

    # Vérifie activation dans Firestore
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # Vérifie présence du range
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        return
    range_data = range_doc.to_dict()
    high_15, low_15 = range_data["high"], range_data["low"]

    # Récupère les news pertinentes
    news_docs = db.collection("polygon_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("published_utc", ">=", f"{today}T00:00:00Z") \
        .stream()

    news_summary = "\n".join([n.to_dict().get("summary", "") for n in news_docs])

    # Génère le prompt pour GPT
    prompt = f"""
    Tu es un day trader expérimenté. Voici les données :
    - Range des 15 premières minutes : High = {high_15}, Low = {low_15}
    - Dernière bougie : open = {candle['o']}, high = {candle['h']}, low = {candle['l']}, close = {candle['c']}
    - News du jour :\n{news_summary}

    Faut-il entrer en position maintenant ? Réponds en JSON :
    {{
      "direction": "long" ou "short",
      "justification": "...",
      "risk_level": float (0.1 à 1.0)
    }}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu analyses bougies et news pour détecter des breakout ou fake breakout et prendre un trade intraday."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.3
        )

        decision = eval(response.choices[0].message.content.strip())
        if decision.get("risk_level", 0) < 0.5:
            return

        direction = decision["direction"].upper()
        entry = get_entry_price()
        sl_ref = candle["l"] if direction == "LONG" else candle["h"]
        sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref, direction)

        if risk_per_unit == 0:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul", level="ERROR")
            return

        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
            return

        # Vérifie si déjà exécutée
        trade_doc = db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).get()
        if trade_doc.exists:
            log_to_firestore(f"🔁 [{STRATEGY_KEY}] Déjà exécuté aujourd'hui", level="TRADING")
            return

        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Trade {direction} exécuté : {executed_units} unités", level="TRADING")

        db.collection("trading_days").document(today).collection("trades").document(STRATEGY_KEY).set({
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed_units,
            "timestamp": datetime.now().isoformat(),
            "meta": {
                "justification": decision.get("justification"),
                "risk_level": decision.get("risk_level")
            }
        })

    except Exception as e:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Erreur GPT : {e}", level="ERROR")
