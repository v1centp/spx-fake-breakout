# app/strategies/ichimoku_strategy.py
from datetime import datetime, timezone
from app.services.firebase import get_firestore
from app.services.log_service import log_to_firestore, log_trade_event
from app.config.instrument_map import resolve_instrument
from app.services.calendar_service import check_high_impact_nearby, get_all_upcoming_events
from app.services.ichimoku_analyzer import rule_based_filter, gpt_macro_analysis
from app.services.shared_strategy_tools import (
    get_entry_price, calculate_sl_tp, compute_position_size, execute_trade
)

STRATEGY_KEY = "ichimoku"
DEFAULT_RISK_CHF = 50
DEFAULT_RISK_USD = 50
MIN_CONFIDENCE = 60


def process_webhook_signal(body: dict) -> dict:
    """Pipeline complet pour un signal webhook TradingView Ichimoku."""

    tv_symbol = body.get("symbol")
    direction = body.get("direction")

    # 1. Resoudre instrument
    inst_cfg = resolve_instrument(tv_symbol)
    if not inst_cfg:
        log_to_firestore(f"[{STRATEGY_KEY}] Instrument inconnu: {tv_symbol}", level="WEBHOOK")
        return {"status": "REJECT", "reason": f"Unknown instrument: {tv_symbol}"}

    broker = inst_cfg.get("broker", "oanda")
    # Instrument identifier: "oanda" field for OANDA, "pair" field for Kraken
    instrument = inst_cfg.get("pair") if broker == "kraken" else inst_cfg["oanda"]
    decimals = inst_cfg["decimals"]
    step = inst_cfg["step"]
    tp_ratio = inst_cfg.get("tp_ratio", 2.0)
    sl_buffer = inst_cfg.get("sl_buffer", 0)

    db = get_firestore()

    # 2. Verifier strategie active
    strat_cfg = db.collection("config").document("strategies").get().to_dict() or {}
    if not strat_cfg.get(STRATEGY_KEY, False):
        log_to_firestore(f"[{STRATEGY_KEY}] Strategie desactivee, signal ignore", level="WEBHOOK")
        return {"status": "SKIP", "reason": "Strategy disabled"}

    # 3. Filtre rule-based Ichimoku
    signal = {
        "instrument": instrument,
        "direction": direction,
        "close": float(body["close"]),
        "tenkan": float(body["tenkan"]),
        "kijun": float(body["kijun"]),
        "ssa": float(body["ssa"]),
        "ssb": float(body["ssb"]),
        "chikou": float(body["chikou"]) if body.get("chikou") else None,
        "chikou_ref_price": float(body["chikou_ref_price"]) if body.get("chikou_ref_price") else None,
    }

    rb_result = rule_based_filter(signal)
    if not rb_result["valid"]:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Rule-based REJECT ({instrument} {direction}): {rb_result['reasons']}",
            level="WEBHOOK"
        )
        return {"status": "REJECT", "reason": "Rule-based filter failed", "details": rb_result}

    log_to_firestore(
        f"[{STRATEGY_KEY}] Rule-based OK ({instrument} {direction}): {rb_result['reasons']}",
        level="WEBHOOK"
    )

    # 4. Check calendrier economique — skip for crypto (no macro events)
    news_check = None
    macro_result = None

    if broker == "oanda":
        news_check = check_high_impact_nearby(instrument)
        if news_check["blocked"]:
            nearby_titles = [e["title"] for e in news_check["nearby_events"]]
            log_to_firestore(
                f"[{STRATEGY_KEY}] NO_GO: high-impact news proche pour {instrument}: {nearby_titles}",
                level="WEBHOOK"
            )
            db.collection("strategies").document(STRATEGY_KEY).collection("gpt_rejections").add({
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instrument": instrument,
                "signal_direction": direction,
                "gpt_bias": "N/A",
                "gpt_confidence": None,
                "gpt_analysis": None,
                "rejection_type": "news",
                "news_check": news_check,
                "ichimoku_reasons": rb_result["reasons"],
                "signal_data": signal,
            })
            return {"status": "REJECT", "reason": "High-impact economic event nearby", "news_check": news_check}
    else:
        log_to_firestore(
            f"[{STRATEGY_KEY}] Crypto ({broker}): news check & GPT macro skipped",
            level="WEBHOOK"
        )

    # 5. GPT : analyse macro globale → biais directionnel (OANDA only)
    if broker == "oanda":
        all_events = get_all_upcoming_events()
        macro_result = gpt_macro_analysis(instrument, all_events)

        log_to_firestore(
            f"[{STRATEGY_KEY}] GPT Macro: {macro_result.get('bias')} (confidence: {macro_result.get('confidence')}) - {macro_result.get('analysis', '')[:100]}",
            level="WEBHOOK"
        )

        # Verifier alignement biais macro / direction du trade
        macro_bias = macro_result.get("bias", "NEUTRAL")
        bias_aligned = (
            (direction == "LONG" and macro_bias == "BULLISH") or
            (direction == "SHORT" and macro_bias == "BEARISH")
        )
        if not bias_aligned and macro_bias != "NEUTRAL":
            db.collection("strategies").document(STRATEGY_KEY).collection("gpt_rejections").add({
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instrument": instrument,
                "signal_direction": direction,
                "gpt_bias": macro_bias,
                "gpt_confidence": macro_result.get("confidence"),
                "gpt_analysis": macro_result.get("analysis"),
                "ichimoku_reasons": rb_result["reasons"],
                "signal_data": signal,
            })
            return {
                "status": "REJECT",
                "reason": f"Macro bias ({macro_bias}) oppose au signal ({direction})",
                "gpt_macro": macro_result
            }

    # 6. Execution
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

    # SL = Kijun-sen + buffer (LONG: en-dessous, SHORT: au-dessus)
    if direction == "LONG":
        sl_level = signal["kijun"] - sl_buffer
    else:
        sl_level = signal["kijun"] + sl_buffer

    # SL/TP
    sl_price, tp_price, risk_per_unit = calculate_sl_tp(
        entry, sl_level, direction, tp_ratio=tp_ratio, decimals=decimals
    )
    if not risk_per_unit:
        log_to_firestore(f"[{STRATEGY_KEY}] Risque nul (entry={entry}, kijun={sl_level})", level="ERROR")
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
    pos_instrument = instrument if broker == "oanda" else None  # no quote conversion for crypto/USD
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

    # 7. Sauvegarder dans Firestore
    trade_id = f"{today}_{instrument}_{direction}"
    trade_ref = db.collection("strategies").document(STRATEGY_KEY).collection("trades").document(trade_id)

    # Generic trade ID field: oanda_trade_id for OANDA, trade_id for Kraken
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
        "ichimoku_reasons": rb_result["reasons"],
    }

    # OANDA-specific fields
    if broker == "oanda":
        trade_data["risk_chf"] = risk_amount
        trade_data["gpt_macro_bias"] = macro_result.get("bias") if macro_result else None
        trade_data["gpt_macro_confidence"] = macro_result.get("confidence") if macro_result else None
        trade_data["gpt_macro_analysis"] = macro_result.get("analysis") if macro_result else None
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
    })

    log_to_firestore(
        f"[{STRATEGY_KEY}] Trade {instrument} {direction} @ {entry} (SL: {sl_price}, TP: {tp_price}) [{broker}]",
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
        response_data["gpt_macro"] = macro_result
        response_data["oanda_trade_id"] = result.get("oanda_trade_id")
    else:
        response_data["kraken_txid"] = result.get("trade_id")

    return response_data
