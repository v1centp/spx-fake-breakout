import os
import json
import html
import re
from datetime import datetime, timezone
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

def get_candle_history(db, day):
    candles = db.collection("ohlc_1m") \
        .where("day", "==", day) \
        .order_by("utc_time") \
        .stream()
    return [
        {"t": c.to_dict()["utc_time"], "o": c.to_dict()["o"], "h": c.to_dict()["h"],
         "l": c.to_dict()["l"], "c": c.to_dict()["c"]}
        for c in candles
    ]

def process(candle):
    db = get_firestore()
    today = candle["day"]

    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        print("⏱️ En dehors de la fenêtre de trading.")
        return

    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        print("❌ Stratégie non activée.")
        return

    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        print("❌ Range d'ouverture non trouvé.")
        return
    range_data = range_doc.to_dict()
    high_15, low_15 = range_data["high"], range_data["low"]

    news_docs = db.collection("all_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("fetched_at", ">=", f"{today}T00:00:00Z") \
        .stream()
    news_summary = "\n".join([n.to_dict().get("summary", "") for n in news_docs])
    safe_news = html.escape(news_summary).replace('"', "'")

    history = get_candle_history(db, today)
    history_text = "\n".join([f"{c['t']} - o:{c['o']} h:{c['h']} l:{c['l']} c:{c['c']}" for c in history[-30:]])

    prompt = (
        f"Historique récent des bougies (UTC) :\n{history_text}\n\n"
        f"Range d'ouverture : High = {high_15}, Low = {low_15}\n"
        f"Dernière bougie : o={candle['o']}, h={candle['h']}, l={candle['l']}, c={candle['c']}\n"
        f"News importantes du jour :\n{safe_news}\n\n"
        "Dois-je entrer un trade ? Si oui, choisis :\n"
        "- direction: long ou short\n"
        "- sl_ref: le niveau de stop idéal (technique)\n"
        "- tp_ratio: ratio TP/SL recommandé (au moins 2.0)\n"
        "Réponds uniquement avec un JSON de ce format :\n"
        '{\n'
        '  "prendre_position": true ou false,\n'
        '  "direction": "long" ou "short",\n'
        '  "sl_ref": float,\n'
        '  "tp_ratio": float,\n'
        '  "justification": "explication concise"\n'
        '}'
    )

    print("📄 Prompt généré :", prompt)

    try:
        print("📤 Envoi du prompt à GPT...")
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu es un trader professionnel. Tu aides à placer des trades intraday en analysant les bougies et les news. Respecte toujours un TP ratio ≥ 2."},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.3
        )
        gpt_reply = response.choices[0].message.content.strip()
        print("📥 Réponse GPT brute :", gpt_reply)
        log_to_firestore(f"📥 [{STRATEGY_KEY}] Réponse brute : {gpt_reply}", level="GPT")

        json_match = re.search(r"{.*}", gpt_reply, re.DOTALL)
        if not json_match:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] JSON introuvable dans réponse GPT", level="ERROR")
            return

        decision = json.loads(json_match.group())
        if not decision.get("prendre_position", False):
            log_to_firestore(f"🟡 [{STRATEGY_KEY}] Pas de position recommandée", level="TRADING")
            return

        direction = decision["direction"].upper()
        sl_ref = float(decision["sl_ref"])
        tp_ratio = float(decision["tp_ratio"])

        # 📈 Prix d'entrée actuel
        entry = get_entry_price()
        risk_per_unit = abs(entry - sl_ref)

        if risk_per_unit == 0 or tp_ratio < 1.5:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul ou ratio trop faible", level="ERROR")
            return

        tp_price = (
            entry + tp_ratio * risk_per_unit if direction == "LONG"
            else entry - tp_ratio * risk_per_unit
        )

        sl_price = sl_ref
        units = compute_position_size(risk_per_unit, RISK_CHF)
        if units < 0.1:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
            return

        # 🔁 Limite de 5 trades max pour cette stratégie
        trades_today = db.collection("trading_days").document(today).collection("trades") \
            .where("strategy", "==", STRATEGY_KEY).stream()
        if len(list(trades_today)) >= 5:
            log_to_firestore(f"🚫 [{STRATEGY_KEY}] 5 trades déjà exécutés aujourd'hui", level="TRADING")
            return

        executed_units = execute_trade(entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Trade {direction} exécuté : {executed_units} unités", level="TRADING")

        db.collection("trading_days").document(today).collection("trades").add({
            "strategy": STRATEGY_KEY,
            "entry": entry,
            "sl": sl_price,
            "tp": tp_price,
            "direction": direction,
            "units": executed_units,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "meta": {
                "justification": decision.get("justification"),
                "tp_ratio": tp_ratio,
                "sl_ref": sl_ref,
                "prendre_position": True
            }
        })

    except Exception as e:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Erreur GPT : {e}", level="ERROR")
        print(f"❌ Erreur GPT : {e}")
