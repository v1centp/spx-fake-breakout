# app/strategies/nasdaq_trend_follow.py
from datetime import datetime, timezone, timedelta
import pytz
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore, log_trade_event
from app.config.universe import UNIVERSE
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "trend_follow"
DEFAULT_RISK_CHF = 50


def _session_for(sym: str):
    s = UNIVERSE.get(sym, {}).get("session", {})
    tz = pytz.timezone(s.get("tz", "America/New_York"))
    oh, om = map(int, s.get("open", "09:30").split(":"))
    or_min = int(s.get("or_minutes", 15))
    th, tm = map(int, s.get("trade_end", "11:30").split(":"))
    return tz, oh, om, or_min, th, tm


def process(candle: dict):
    if candle["sym"] != "I:NDX":
        return

    db = get_firestore()

    sym = candle["sym"]
    today = candle["day"]
    cfg = UNIVERSE.get(sym)
    if not cfg or not cfg.get("active"):
        return

    instrument = cfg["instrument"]
    settings = db.collection("config").document("settings").get().to_dict() or {}
    risk_chf = settings.get("risk_chf", DEFAULT_RISK_CHF)

    # Fenetre horaire locale
    utc_dt = datetime.strptime(candle["utc_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    tz, oh, om, or_min, th, tm = _session_for(sym)
    loc = utc_dt.astimezone(tz)
    open_start = loc.replace(hour=oh, minute=om, second=0, microsecond=0)
    open_end = open_start + timedelta(minutes=or_min)
    trade_end = loc.replace(hour=th, minute=tm, second=0, microsecond=0)

    if loc < open_end or loc > trade_end:
        return

    # Activation via config Firestore
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        return

    # Opening range
    rdoc = db.collection("opening_range").document(f"{today}_{sym}").get().to_dict()
    if not rdoc or rdoc.get("status") != "ready":
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] opening_range manquant ({today}_{sym})", level="INFO")
        return

    high_15, low_15 = float(rdoc["high"]), float(rdoc["low"])
    o, c = float(candle["o"]), float(candle["c"])
    candle_id = f"{sym}_{candle['e']}"

    # Signal trend following (ORB strict)
    # LONG  : open DANS le range ET close AU-DESSUS
    # SHORT : open DANS le range ET close EN-DESSOUS
    direction = None
    if low_15 <= o <= high_15 and c > high_15:
        direction = "LONG"
    elif low_15 <= o <= high_15 and c < low_15:
        direction = "SHORT"
    else:
        db.collection("ohlc_1m").document(candle_id).update(
            {f"strategy_decisions.{STRATEGY_KEY}": "REJECT: conditions non remplies"}
        )
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Conditions non remplies", level="NO_TRADING")
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
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Trade {direction} deja execute aujourd'hui.", level="TRADING")
        return

    log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Signal {direction} detecte", level="TRADING")

    # Prix d'entree OANDA
    try:
        entry = float(get_entry_price(instrument))
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Prix {instrument} : {entry}", level="OANDA")
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Erreur prix OANDA : {e}", level="ERROR")
        return

    # SL = milieu du range
    sl_midpoint = (high_15 + low_15) / 2

    # SL/TP (TP at 3R for scaling-out: 50% at 1R, 25% at 2R, 25% at 3R)
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(entry, sl_midpoint, direction, tp_ratio=3.0)
    if not risk_per_unit:
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Risque nul.", level="ERROR")
        return

    # Position sizing
    units = compute_position_size(risk_per_unit, risk_chf)
    if units < 0.1:
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Taille position trop faible ({units})", level="ERROR")
        return

    # Execution
    try:
        result = execute_trade(instrument, entry, sl_price, tp_price, units, direction)
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Ordre {direction} execute ({result['units']})", level="TRADING")
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}::{sym}] Erreur execution : {e}", level="ERROR")
        return

    # Enregistrement trade
    _, trade_ref = db.collection("trading_days").document(today)\
      .collection("symbols").document(sym)\
      .collection("trades").add({
        "strategy": STRATEGY_KEY,
        "instrument": instrument,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": result["units"],
        "timestamp": datetime.now().isoformat(),
        "source_candle_id": candle_id,
        "outcome": "open",
        "oanda_trade_id": result.get("oanda_trade_id"),
        "fill_price": result.get("fill_price"),
        "breakeven_applied": False,
        "scaling_step": 0,
        "initial_units": abs(result["units"]),
        "risk_r": risk_per_unit,
        "step": 0.1,
    })

    log_trade_event(trade_ref, "OPENED", f"Trade {direction} ouvert sur {instrument}", {
        "entry": entry,
        "fill_price": result.get("fill_price"),
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": result["units"],
        "instrument": instrument,
        "oanda_trade_id": result.get("oanda_trade_id"),
    })

    log_to_firestore(
        f"[{STRATEGY_KEY}::{sym}] Trade {instrument} @ {entry} (SL: {sl_price}, TP: {tp_price})",
        level="TRADING"
    )
