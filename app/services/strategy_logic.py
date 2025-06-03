from app.services.firebase import get_firestore
from app.services import oanda_service
from datetime import datetime
import pytz
from app.services.log_service import log_to_firestore
from math import floor


def process_new_minute_bar(bar: dict):
    db = get_firestore()
    today = bar["day"]
    ny_time = datetime.strptime(bar["utc_time"], "%Y-%m-%d %H:%M:%S").astimezone(pytz.timezone("America/New_York")).time()

    # 🕒 Vérification de la fenêtre horaire
    if not (datetime.strptime("09:45", "%H:%M").time() <= ny_time <= datetime.strptime("11:30", "%H:%M").time()):
        print(f"⏱️ {bar['utc_time']} ignorée : hors fenêtre de trading (09:45–11:30 NY)")
        #log_to_firestore(f"⏱️ {bar['utc_time']} ignorée : hors fenêtre de trading (09:45–11:30 NY)")
        return

    # ✅ Vérifier si la stratégie est activée
    strategy_doc = db.collection("config").document("strategies").get()
    if not strategy_doc.exists or not strategy_doc.to_dict().get("sp500_fake_breakout_active"):
        print("❌ Stratégie SP500 désactivée dans Firestore.")
        log_to_firestore("❌ Stratégie SP500 désactivée dans Firestore.")
        
        return

    # 📏 Vérifier si le range du jour est prêt
    range_doc = db.collection("opening_range").document(today).get()
    if not range_doc.exists or range_doc.to_dict().get("status") != "ready":
        print(f"📉 Range non prêt pour {today}.")
        log_to_firestore(f"📉 Range non prêt pour {today}.")

        return

    range_data = range_doc.to_dict()
    high_15, low_15, range_size = range_data["high"], range_data["low"], range_data["range_size"]
    print(f"📊 Opening Range {today} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}")
    log_to_firestore(f"📊 Opening Range {today} — High: {high_15}, Low: {low_15}, Size: {range_size:.2f}")
    

    # ❌ Ne pas trader plusieurs fois le même jour
    trade_doc = db.collection("trading_days").document(today).get()
    if trade_doc.exists and trade_doc.to_dict().get("executed"):
        print(f"🔁 Trade déjà exécuté pour {today}.")
        log_to_firestore(f"🔁 Trade déjà exécuté pour {today}.")
        
        return

    # 📈 Conditions de breakout
    direction = None
    if bar["h"] > high_15 and low_15 <= bar["c"] <= high_15:
        breakout = bar["h"] - high_15
        if breakout >= 0.15 * range_size:
            direction = "SHORT"
            print(f"📉 Breakout SHORT détecté. Excès: {breakout:.2f}")
            log_to_firestore(f"📉 Breakout SHORT détecté. Excès: {breakout:.2f}")
            
        else:
            print(f"↩️ Excès SHORT insuffisant ({breakout:.2f} < 15% du range)")
            log_to_firestore(f"↩️ Excès SHORT insuffisant ({breakout:.2f} < 15% du range)")
    elif bar["l"] < low_15 and low_15 <= bar["c"] <= high_15:
        breakout = low_15 - bar["l"]
        if breakout >= 0.15 * range_size:
            direction = "LONG"
            print(f"📈 Breakout LONG détecté. Excès: {breakout:.2f}")
            log_to_firestore(f"📈 Breakout LONG détecté. Excès: {breakout:.2f}")
        else:
            print(f"↩️ Excès LONG insuffisant ({breakout:.2f} < 15% du range)")
            log_to_firestore(f"↩️ Excès LONG insuffisant ({breakout:.2f} < 15% du range)")

    if not direction:
        print("🔍 Aucune condition de breakout valide détectée.")
        log_to_firestore("🔍 Aucune condition de breakout valide détectée.")
        return

    # 🎯 Récupération du prix OANDA
    try:
        oanda_price = oanda_service.get_latest_price("SPX500_USD")
        entry_price = oanda_price
        print(f"💵 Prix OANDA pour exécution : {entry_price}")
        log_to_firestore(f"💵 Prix OANDA pour exécution : {entry_price}")
    except Exception as e:
        print(f"⚠️ Erreur récupération prix OANDA : {e}")
        log_to_firestore(f"⚠️ Erreur récupération prix OANDA : {e}")
        return

    # 🛡️ Stop Loss & Take Profit
        # 🔁 Conversion du SL (issu du SPX) vers niveau CFD
    last_spx_close = bar["c"]  # close de la dernière bougie minute SPX
    spread_factor = entry_price / last_spx_close  # ajustement CFD vs SPX

    sl_spx = low_15 if direction == "LONG" else high_15
    stop_loss_price = sl_spx * spread_factor

    # 📏 Calcul TP dynamique
    if direction == "LONG":
        take_profit_price = entry_price + 1.75 * (entry_price - stop_loss_price)
    else:
        take_profit_price = entry_price - 1.75 * (stop_loss_price - entry_price)

    # 📐 Calcul taille de position
    risk_per_unit = abs(entry_price - stop_loss_price)
    if risk_per_unit == 0:
        print("❌ Risque par unité nul, impossible de trader.")
        log_to_firestore("❌ Risque par unité nul, impossible de trader.")
        return

    units = floor(50 / risk_per_unit)
    if direction == "SHORT":
        units = -units


    try:
        oanda_service.create_order(
    instrument="SPX500_USD",
    entry_price=entry_price,
    stop_loss_price=stop_loss_price,
    take_profit_price=take_profit_price,
    units=units
)

        print(f"✅ Ordre {direction} placé chez OANDA : {units} unités")
        log_to_firestore(f"✅ Ordre {direction} placé chez OANDA : {units} unités")
    except Exception as e:
        print(f"⚠️ Erreur exécution ordre OANDA : {e}")
        log_to_firestore(f"⚠️ Erreur exécution ordre OANDA : {e}")
        return

    # 💾 Enregistrer l'exécution
    db.collection("trading_days").document(today).set({
        "executed": True,
        "entry": entry_price,
        "sl": round(stop_loss_price, 2),
        "tp": round(take_profit_price, 2),
        "direction": direction,
        "timestamp": datetime.now().isoformat()
    })
    print(f"🚀 Signal {direction} exécuté à {entry_price} (SL: {round(stop_loss_price, 2)}, TP: {round(take_profit_price, 2)})")
    log_to_firestore(f"🚀 Signal {direction} exécuté à {entry_price} (SL: {round(stop_loss_price, 2)}, TP: {round(take_profit_price, 2)})")

