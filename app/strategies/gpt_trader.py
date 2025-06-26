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

    # ⏱️ Vérifie plage horaire (entre 09:45 et 11:30 NY)
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    ny_time = utc_dt.astimezone(pytz.timezone("America/New_York")).time()
    if ny_time < datetime.strptime("09:45", "%H:%M").time() or ny_time > datetime.strptime("11:30", "%H:%M").time():
        print("⏱️ En dehors de la fenêtre de trading.")
        return

    # ✅ Vérifie activation dans Firestore
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        print("❌ Stratégie non activée.")
        return

    # 📊 Vérifie présence du range
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists:
        print("❌ Range d'ouverture non trouvé.")
        return
    range_data = range_doc.to_dict()
    high_15, low_15 = range_data["high"], range_data["low"]

    # 📰 Récupère les news pertinentes
    news_docs = db.collection("all_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("fetched_at", ">=", f"{today}T00:00:00Z") \
        .stream()
    news_summary = "\n".join([n.to_dict().get("summary", "") for n in news_docs])

    # 🤖 Génère le prompt
    safe_news = html.escape(news_summary).replace('"', "'")
    prompt = (
        f"Range des 15 premières minutes : High = {high_15}, Low = {low_15}\n"
        f"Dernière bougie : o={candle['o']}, h={candle['h']}, l={candle['l']}, c={candle['c']}\n"
        f"News importantes du jour :\n{safe_news}\n\n"
        "Analyse les données et dis-moi si je dois entrer un trade maintenant.\n"
        "Réponds uniquement avec un JSON de cette forme :\n"
        '{\n'
        '  "prendre_position": true ou false,\n'
        '  "direction": "long" ou "short",\n'
        '  "justification": "ta justification détaillée"\n'
        '}'
    )

    print("📄 Prompt généré :", prompt)

    try:
        print("📤 Envoi du prompt à GPT...")
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tu analyses bougies et news pour détecter des breakout ou fake breakout et prendre un trade intraday."},
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
            print("🟡 GPT ne recommande pas de prise de position.")
            log_to_firestore(f"🟡 [{STRATEGY_KEY}] Pas de position recommandée", level="TRADING")
            return

        direction = decision["direction"].upper()

        # 🚫 Limite de 5 trades par jour
        trades_today = list(db.collection("trading_days").document(today).collection("trades").stream())
        if len(trades_today) >= 5:
            log_to_firestore(f"🚫 [{STRATEGY_KEY}] 5 trades déjà exécutés aujourd'hui", level="TRADING")
            return

        # 🔁 Empêche de reprendre un trade identique
        for t in trades_today:
            t_data = t.to_dict()
            if t_data.get("direction") == direction:
                log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade {direction} déjà pris aujourd'hui", level="TRADING")
                return

        entry = get_entry_price()
        sl_ref = candle["l"]-5 if direction == "LONG" else candle["h"]+5
        sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref, direction)

        if risk_per_unit == 0:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul", level="ERROR")
            return

        units = compute_position_size(risk_per_unit, RISK_CHF)
        print(f"📊 Calculs - Entry: {entry}, SL: {sl_price}, TP: {tp_price}, Risk/unit: {risk_per_unit}, Units: {units}")
        log_to_firestore(f"[DEBUG] Entry={entry}, SL={sl_price}, TP={tp_price}, Risk/Unit={risk_per_unit}, Units={units}", level="INFO")

        if units < 0.1:
            log_to_firestore(f"❌ [{STRATEGY_KEY}] Position trop petite ({units})", level="ERROR")
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
                "prendre_position": True
            }
        })

    except Exception as e:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Erreur GPT : {e}", level="ERROR")
        print(f"❌ Erreur GPT : {e}")
