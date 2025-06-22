from app.services.oanda_service import get_latest_price, create_order
from app.services.log_service import log_to_firestore
from datetime import datetime
from math import floor

STRATEGY_KEY = "sp500_fake_breakout_strict"
RISK_CHF = 50

def process(bar, db, today, high_15, low_15, range_size):
    # ✅ Vérifie que la stratégie est activée
    config = db.collection("config").document("strategies").get().to_dict()
    if not config.get(STRATEGY_KEY, False):
        return

    # 🔁 Vérifie si un trade a déjà été exécuté
    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get("executed", False):
        log_to_firestore(f"🔁 [{STRATEGY_KEY}] Trade déjà exécuté pour {today}", level="TRADING")
        return

    # 🎯 Détection du breakout strict
    direction = None
    breakout = 0
    if bar["h"] > high_15 and low_15 <= bar["c"] <= high_15:
        breakout = bar["h"] - high_15
        if breakout >= 0.15 * range_size and bar["o"] >= low_15:
            direction = "SHORT"
    elif bar["l"] < low_15 and low_15 <= bar["c"] <= high_15:
        breakout = low_15 - bar["l"]
        if breakout >= 0.15 * range_size and bar["o"] <= high_15:
            direction = "LONG"

    if not direction:
        log_to_firestore(f"🔍 [{STRATEGY_KEY}] Aucun breakout valide détecté.", level="NO_TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}] {'📈' if direction == 'LONG' else '📉'} Signal {direction} détecté. Excès: {breakout:.2f}", level="TRADING")

    # 💵 Récupère le prix d'entrée
    try:
        entry_price = get_latest_price("SPX500_USD")
        log_to_firestore(f"💵 [{STRATEGY_KEY}] Prix OANDA : {entry_price}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur récupération prix OANDA : {e}", level="ERROR")
        return

    # 📏 Calcul du SL / TP
    spread_factor = entry_price / bar["c"]
    sl_ref = low_15 if direction == "LONG" else high_15
    sl_price = round(sl_ref * spread_factor, 2)
    risk_per_unit = abs(entry_price - sl_price)
    if risk_per_unit == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Risque nul, trade ignoré", level="ERROR")
        return

    tp_price = round(entry_price + 1.75 * risk_per_unit if direction == "LONG" else entry_price - 1.75 * risk_per_unit, 2)
    units = floor(RISK_CHF / risk_per_unit)

    if units < 1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}] Taille de position trop faible ({units}), ignoré.", level="ERROR")
        return

    # ✅ Envoie l’ordre
    try:
        executed_units = -units if direction == "SHORT" else units
        create_order(
            instrument="SPX500_USD",
            entry_price=entry_price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            units=executed_units
        )
        log_to_firestore(f"✅ [{STRATEGY_KEY}] Ordre {direction} exécuté ({executed_units} unités)", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}] Erreur exécution ordre : {e}", level="ERROR")
        return

    # 📝 Enregistre l’exécution
    db.collection("trading_days").document(today).set({
        "executed": True,
        "entry": entry_price,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": executed_units,
        "timestamp": datetime.now().isoformat()
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}] Trade exécuté à {entry_price} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
