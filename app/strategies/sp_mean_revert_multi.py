# app/strategies/sp_mean_revert_multi.py
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.config.universe import UNIVERSE
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "mean_revert"
DEFAULT_RISK_CHF = 50

def _session_for(sym: str):
    s = UNIVERSE.get(sym, {}).get("session", {})
    tz = pytz.timezone(s.get("tz", "America/New_York"))
    oh, om = map(int, s.get("open", "09:30").split(":"))
    or_min = int(s.get("or_minutes", 15))
    th, tm = map(int, s.get("trade_end", "11:30").split(":"))
    return tz, oh, om, or_min, th, tm

def process(candle: dict):
    db = get_firestore()

    sym = candle["sym"]                 # EXACTEMENT le symbole Polygon (ex: "I:SPX")
    today = candle["day"]               # "YYYY-MM-DD"
    cfg = UNIVERSE.get(sym)
    if not cfg or not cfg.get("active"):
        return

    instrument = cfg["instrument"]
    risk_chf   = cfg.get("risk_chf", DEFAULT_RISK_CHF)

    # Fenêtre horaire locale par symbole
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    tz, oh, om, or_min, th, tm = _session_for(sym)
    loc = utc_dt.astimezone(tz)
    open_start = loc.replace(hour=oh, minute=om, second=0, microsecond=0)
    open_end   = open_start + timedelta(minutes=or_min)
    trade_end  = loc.replace(hour=th, minute=tm, second=0, microsecond=0)

    # On ne trade qu'entre fin du range d'ouverture et fin de session
    if loc < open_end or loc > trade_end:
        return

    # Activation via config Firestore
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        return

    # Range d’ouverture (doc clé = f"{day}_{sym}")
    rdoc = db.collection("opening_range").document(f"{today}_{sym}").get().to_dict()
    if not rdoc or rdoc.get("status") != "ready":
        log_to_firestore(f"⏳ [{STRATEGY_KEY}::{sym}] opening_range manquant ({today}_{sym})", level="INFO")
        return

    high_15, low_15 = float(rdoc["high"]), float(rdoc["low"])
    o, c = float(candle["o"]), float(candle["c"])
    candle_id = f"{sym}_{candle['e']}"

    # Logique mean-revert
    direction = None
    if o > high_15 and low_15 <= c <= high_15:
        direction = "SHORT"
        # SL de référence = plus haut du jour (côté Polygon) pour ce symbole
        highs = [x.to_dict().get("h") for x in db.collection("ohlc_1m")
                 .where("day", "==", today).where("sym", "==", sym).stream()]
        highs = [float(h) for h in highs if h is not None]
        if not highs:
            db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": "REJECT: no highs"})
            return
        sl_ref_polygon = max(highs)
    elif o < low_15 and low_15 <= c <= high_15:
        direction = "LONG"
        lows = [x.to_dict().get("l") for x in db.collection("ohlc_1m")
                .where("day", "==", today).where("sym", "==", sym).stream()]
        lows = [float(l) for l in lows if l is not None]
        if not lows:
            db.collection("ohlc_1m").document(candle_id).update({f"strategy_decisions.{STRATEGY_KEY}": "REJECT: no lows"})
            return
        sl_ref_polygon = min(lows)
    else:
        db.collection("ohlc_1m").document(candle_id).update(
            {f"strategy_decisions.{STRATEGY_KEY}": "REJECT: conditions non remplies"}
        )
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] Conditions non remplies", level="NO_TRADING")
        return

    db.collection("ohlc_1m").document(candle_id).update(
        {f"strategy_decisions.{STRATEGY_KEY}": f"ACCEPT: {direction}"}
    )

    # 1 trade / jour / symbole / direction
    trades_same_dir = list(
        db.collection("trading_days").document(today)
          .collection("symbols").document(sym)
          .collection("trades")
          .where("strategy", "==", STRATEGY_KEY)
          .where("direction", "==", direction)
          .stream()
    )
    if trades_same_dir:
        log_to_firestore(f"🔁 [{STRATEGY_KEY}::{sym}] Trade {direction} déjà exécuté aujourd'hui.", level="TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}::{sym}] 📌 Signal {direction} détecté", level="TRADING")

    # Prix d’entrée OANDA
    try:
        entry = float(get_entry_price(instrument))
        log_to_firestore(f"💵 [{STRATEGY_KEY}::{sym}] Prix {instrument} : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}::{sym}] Erreur prix OANDA : {e}", level="ERROR")
        return

    # Ajustement SL avec spread factor (Polygon → OANDA)
    if c == 0:
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] close=0, division impossible.", level="ERROR")
        return
    sl_ref_oanda = float(sl_ref_polygon) * (entry / c)

    # SL/TP
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_ref_oanda, direction)
    if not risk_per_unit:
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] Risque nul.", level="ERROR")
        return

    # Position sizing
    units = compute_position_size(risk_per_unit, risk_chf)
    if units < 0.1:
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] Taille position trop faible ({units})", level="ERROR")
        return

    # Exécution
    try:
        executed_units = execute_trade(instrument, entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"✅ [{STRATEGY_KEY}::{sym}] Ordre {direction} exécuté ({executed_units})", level="TRADING")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}::{sym}] Erreur exécution : {e}", level="ERROR")
        return

    # Enregistrement trade
    db.collection("trading_days").document(today)\
      .collection("symbols").document(sym)\
      .collection("trades").add({
        "strategy": STRATEGY_KEY,
        "instrument": instrument,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": executed_units,
        "timestamp": datetime.now().isoformat(),
        "source_candle_id": candle_id,
        "outcome": "unknown",
    })

    log_to_firestore(
        f"🚀 [{STRATEGY_KEY}::{sym}] Trade {instrument} @ {entry} (SL: {sl_price}, TP: {tp_price})",
        level="TRADING"
    )
