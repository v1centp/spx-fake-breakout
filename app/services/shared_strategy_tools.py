# app/services/shared_strategy_tools.py
import math
from app.services import oanda_service

STEP = 0.1  # pas OANDA

def _floor_step(x: float, step: float = STEP) -> float:
    """Arrondit vers le bas au multiple de `step` (ex: 0.37 -> 0.3)."""
    if step <= 0:
        return 0.0
    return math.floor(x / step) * step

def get_entry_price(instrument: str):
    return oanda_service.get_latest_price(instrument)

def calculate_sl_tp(entry, sl_level, direction, tp_ratio=2.75, decimals=2):
    risk = abs(entry - sl_level)
    if risk == 0:
        return None, None, 0
    tp = entry + tp_ratio * risk if direction == "LONG" else entry - tp_ratio * risk
    return round(sl_level, decimals), round(tp, decimals), risk

def compute_position_size(risk_per_unit, risk_limit=50, step=None):
    """Taille théorique puis **floor** au pas configurable (pour respecter le risque max)."""
    if step is None:
        step = STEP
    if risk_per_unit <= 0:
        return 0.0
    raw = risk_limit / risk_per_unit
    return _floor_step(raw, step)

def execute_trade(instrument: str, entry_price, sl_price, tp_price, units, direction, step=None):
    """
    Envoie un multiple **exact** du step configurable :
    - on normalise la quantité au pas donné
    - on applique le signe selon la direction
    """
    if step is None:
        step = STEP
    qty = _floor_step(abs(float(units)), step)
    print(f"execute_trade: {units} -> {qty} ({direction})")
    if qty < step:
        raise ValueError(f"units too small (< {step}): {units}")
    signed = -qty if direction == "SHORT" else qty

    response = oanda_service.create_order(
        instrument=instrument,
        entry_price=entry_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        units=signed
    )

    # Extract trade ID and fill price from OANDA response
    oanda_trade_id = None
    fill_price = None
    try:
        fill_tx = response.get("orderFillTransaction", {})
        fill_price = float(fill_tx.get("price", 0))
        # tradeOpened for new trades, tradeReduced for existing position adjustments
        opened = fill_tx.get("tradeOpened")
        if opened:
            oanda_trade_id = opened["tradeID"]
        else:
            reduced = fill_tx.get("tradeReduced")
            if reduced:
                oanda_trade_id = reduced["tradeID"]
            else:
                # Fallback: check tradesOpened (list)
                trades_opened = fill_tx.get("tradesOpened", [])
                if trades_opened:
                    oanda_trade_id = trades_opened[0]["tradeID"]
    except Exception:
        pass

    return {
        "units": signed,
        "oanda_trade_id": oanda_trade_id,
        "fill_price": fill_price,
    }
