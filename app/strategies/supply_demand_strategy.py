# app/strategies/supply_demand_strategy.py
from datetime import datetime, timezone
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore, log_trade_event
from app.config.instrument_map import resolve_instrument
from app.services.calendar_service import check_high_impact_nearby
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "supply_demand"
DEFAULT_RISK_CHF = 50
DEFAULT_RISK_USD = 50


def process_webhook_signal(body: dict) -> dict:
    """Pipeline complet pour un signal webhook TradingView Supply & Demand BOS."""

    tv_symbol = body.get("symbol")
    direction = body.get("direction")
    zone_top = float(body.get("zone_top", 0))
    zone_bottom = float(body.get("zone_bottom", 0))

    if not zone_top or not zone_bottom:
        log_to_firestore(f"[{STRATEGY_KEY}] Zone manquante: top={zone_top}, bottom={zone_bottom}", level="WEBHOOK")
        return {"status": "REJECT", "reason": "Missing zone_top or zone_bottom"}

    # 1. Resoudre instrument
    inst_cfg = resolve_instrument(tv_symbol)
    if not inst_cfg:
        log_to_firestore(f"[{STRATEGY_KEY}] Instrument inconnu: {tv_symbol}", level="WEBHOOK")
        return {"status": "REJECT", "reason": f"Unknown instrument: {tv_symbol}"}

    broker = inst_cfg.get("broker", "oanda")
    instrument = inst_cfg.get("pair") if broker == "kraken" else inst_cfg["oanda"]
    decimals = inst_cfg["decimals"]
    step = inst_cfg["step"]
    tp_ratio = inst_cfg.get("tp_ratio", 3.0)
    sl_buffer = inst_cfg.get("sl_buffer", 0)

    db = get_firestore()

    # 2. Verifier strategie active
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        log_to_firestore(f"[{STRATEGY_KEY}] Strategie desactivee, signal ignore", level="WEBHOOK")
        return {"status": "SKIP", "reason": "Strategy disabled"}

    # 3. Check calendrier economique — skip for crypto
    news_check = None

    if broker == "oanda":
        news_check = check_high_impact_nearby(instrument)
        if news_check["blocked"]:
            nearby_titles = [e["title"] for e in news_check["nearby_events"]]
            log_to_firestore(
                f"[{STRATEGY_KEY}] NO_GO: high-impact news proche pour {instrument}: {nearby_titles}",
                level="WEBHOOK"
            )
            db.collection("strategies").document(STRATEGY_KEY).collection("rejections").add({
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instrument": instrument,
                "direction": direction,
                "rejection_type": "news",
                "news_check": news_check,
                "zone_top": zone_top,
                "zone_bottom": zone_bottom,
            })
            return {"status": "REJECT", "reason": "High-impact economic event nearby", "news_check": news_check}
    else:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Crypto ({broker}): news check skipped",
            level="WEBHOOK"
        )

    # 4. Execution
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Verifier pas de trade deja ouvert aujourd'hui pour cet instrument + direction
    existing = list(
        db.collection("strategies").document(STRATEGY_KEY)
          .collection("trades")
          .where("date", "==", today)
          .where("instrument", "==", instrument)
          .where("direction", "==", direction)
          .stream()
    )
    if existing:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Trade {direction} deja execute aujourd'hui pour {instrument}",
            level="WEBHOOK"
        )
        return {"status": "SKIP", "reason": "Trade already taken today for this direction"}

    # Prix d'entree
    try:
        entry = float(get_entry_price(instrument, broker=broker))
        log_to_firestore(f"[{STRATEGY_KEY}] Prix {instrument}: {entry}", level=broker.upper())
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Erreur prix {broker}: {e}", level="ERROR")
        return {"status": "ERROR", "reason": f"Price fetch failed: {e}"}

    # SL = zone edge + buffer
    if direction == "LONG":
        sl_level = zone_bottom - sl_buffer
    else:
        sl_level = zone_top + sl_buffer

    # SL/TP
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(
        entry, sl_level, direction, tp_ratio=tp_ratio, decimals=decimals
    )
    if not risk_per_unit:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Risque nul (entry={entry}, sl_level={sl_level})", level="ERROR"
        )
        return {"status": "ERROR", "reason": "Zero risk"}

    # Risk config
    settings = db.collection("config").document("settings").get().to_dict() or {}
    if broker == "kraken":
        risk_amount = settings.get("risk_usd_crypto", DEFAULT_RISK_USD)
        account_currency = "USD"
    else:
        risk_amount = settings.get("risk_chf", DEFAULT_RISK_CHF)
        account_currency = "CHF"

    # Position sizing
    pos_instrument = instrument if broker == "oanda" else None
    units = compute_position_size(
        risk_per_unit, risk_amount, step=step,
        instrument=pos_instrument, account_currency=account_currency,
    )
    if units < step:
        log_to_firestore(f"[{STRATEGY_KEY}] Taille position trop faible ({units})", level="ERROR")
        return {"status": "ERROR", "reason": f"Position too small: {units}"}

    # Execute
    try:
        dry_run = body.get("dry_run", False)
        result = execute_trade(
            instrument, entry, sl_price, tp_price, units, direction,
            step=step, broker=broker, dry_run=dry_run,
        )
        log_to_firestore(
            f"[{STRATEGY_KEY}] Ordre {direction} execute sur {instrument} ({result['units']} units) [{broker}]",
            level="TRADING"
        )
    except Exception as e:
        log_to_firestore(f"[{STRATEGY_KEY}] Erreur execution: {e}", level="ERROR")
        return {"status": "ERROR", "reason": f"Execution failed: {e}"}

    # 5. Sauvegarder dans Firestore
    trade_id = f"{today}_{instrument}_{direction}"
    trade_ref = db.collection("strategies").document(STRATEGY_KEY).collection("trades").document(trade_id)

    trade_id_value = result.get("oanda_trade_id") or result.get("trade_id")

    trade_data = {
        "strategy": STRATEGY_KEY,
        "broker": broker,
        "instrument": instrument,
        "date": today,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": result["units"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": "open",
        "oanda_trade_id": result.get("oanda_trade_id"),
        "trade_id": trade_id_value,
        "tp_txid": result.get("tp_txid"),
        "fill_price": result.get("fill_price"),
        "breakeven_applied": False,
        "scaling_step": 0,
        "initial_units": abs(result["units"]),
        "risk_r": risk_per_unit,
        "risk_amount": risk_amount,
        "step": step,
        "zone_top": zone_top,
        "zone_bottom": zone_bottom,
    }

    if broker == "oanda":
        trade_data["risk_chf"] = risk_amount
        trade_data["news_check"] = news_check

    trade_ref.set(trade_data)

    log_trade_event(trade_ref, "OPENED", f"Trade {direction} ouvert sur {instrument} [{broker}]", {
        "entry": entry,
        "fill_price": result.get("fill_price"),
        "sl": sl_price,
        "tp": tp_price,
        "direction": direction,
        "units": result["units"],
        "instrument": instrument,
        "broker": broker,
        "trade_id": trade_id_value,
        "zone_top": zone_top,
        "zone_bottom": zone_bottom,
    })

    log_to_firestore(
        f"[{STRATEGY_KEY}] Trade {instrument} {direction} @ {entry} "
        f"(SL: {sl_price}, TP: {tp_price}, zone: {zone_bottom}-{zone_top}) [{broker}]",
        level="TRADING"
    )

    response_data = {
        "status": "EXECUTED",
        "trade_id": trade_id,
        "instrument": instrument,
        "direction": direction,
        "entry": entry,
        "sl": sl_price,
        "tp": tp_price,
        "units": result["units"],
        "broker": broker,
    }
    if broker == "oanda":
        response_data["oanda_trade_id"] = result.get("oanda_trade_id")
    else:
        response_data["kraken_txid"] = result.get("trade_id")

    return response_data
