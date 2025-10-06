# app/strategies/sp_mean_revert_multi.py
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore
from app.config.universe import UNIVERSE
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)
from app.utils.symbols import normalize_symbol


STRATEGY_KEY = "mean_revert"
DEFAULT_RISK_CHF = 50

def _session_for(sym):
    s = UNIVERSE.get(sym, {}).get("session", {})
    tz = pytz.timezone(s.get("tz", "America/New_York"))
    oh, om = map(int, s.get("open","09:30").split(":"))
    or_min = int(s.get("or_minutes",15))
    th, tm = map(int, s.get("trade_end","11:30").split(":"))
    return tz, oh, om, or_min, th, tm

def process(candle):
    db = get_firestore()
    sym_raw = candle["sym"]
    sym = normalize_symbol(sym_raw)
    today = candle["day"]     # ✅ FIX: définir 'today' immédiatement
    cfg = UNIVERSE.get(sym)
    if not cfg or not cfg.get("active"):
        log_to_firestore(f"⏭️ [{STRATEGY_KEY}] {sym_raw} ignoré (cfg introuvable pour {sym})", level="INFO")
        return
    instrument = cfg["instrument"]
    risk_chf   = cfg.get("risk_chf", DEFAULT_RISK_CHF)

    # Heure locale par symbole
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    tz, oh, om, or_min, th, tm = _session_for(sym)
    loc = utc_dt.astimezone(tz)
    open_start = loc.replace(hour=oh, minute=om, second=0, microsecond=0)
    open_end   = open_start + timedelta(minutes=or_min)
    trade_end  = loc.replace(hour=th, minute=tm, second=0, microsecond=0)

    # On trade uniquement dans [open_end, trade_end]
    if loc < open_end or loc > trade_end:
        return

    # Activation via config
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        return

    # Range d’ouverture (par symbole)
    range_doc = (db.collection("opening_range")
                   .document(f"{today}_{sym}").get().to_dict())
    if not range_doc or range_doc.get("status") != "ready":
        log_to_firestore(f"⏳ [{STRATEGY_KEY}::{sym}] opening_range manquant ({today}_{sym})", level="INFO")
        return

    high_15, low_15 = range_doc["high"], range_doc["low"]
    o, c = candle["o"], candle["c"]
    candle_id = f"{sym}_{candle['e']}"

    direction = None
    if o > high_15 and low_15 <= c <= high_15:
        direction = "SHORT"
        sl_ref_polygon = max(x.to_dict()["h"] for x in db.collection("ohlc_1m")
                             .where("day", "==", today).where("sym", "==", sym).stream())
    elif o < low_15 and low_15 <= c <= high_15:
        direction = "LONG"
        sl_ref_polygon = min(x.to_dict()["l"] for x in db.collection("ohlc_1m")
                             .where("day", "==", today).where("sym", "==", sym).stream())
    else:
        db.collection("ohlc_1m").document(candle_id).update(
            {f"strategy_decisions.{STRATEGY_KEY}": "REJECT: conditions non remplies"}
        )
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] Conditions non remplies", level="NO_TRADING")
        return

    db.collection("ohlc_1m").document(candle_id).update(
        {f"strategy_decisions.{STRATEGY_KEY}": f"ACCEPT: {direction}"}
    )

    # 1 trade / jour / symbole / stratégie / direction
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
        entry = get_entry_price(instrument)
        log_to_firestore(f"💵 [{STRATEGY_KEY}::{sym}] Prix {instrument} : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"⚠️ [{STRATEGY_KEY}::{sym}] Erreur prix OANDA : {e}", level="ERROR")
        return

    # Ajustement SL avec spread factor
    try:
        spread_factor = entry / c
        sl_ref_oanda = sl_ref_polygon * spread_factor
    except ZeroDivisionError:
        log_to_firestore(f"❌ [{STRATEGY_KEY}::{sym}] c==0, division impossible.", level="ERROR")
        return

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

    # Enregistrement
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
        "outcome": "unknown"
    })

    log_to_firestore(f"🚀 [{STRATEGY_KEY}::{sym}] Trade exécuté {instrument} @ {entry} (SL: {sl_price}, TP: {tp_price})", level="TRADING")
