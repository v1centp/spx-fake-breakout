# app/services/shared_strategy_tools.py
from app.services import oanda_service

def get_entry_price(instrument: str):
    return oanda_service.get_latest_price(instrument)

def calculate_sl_tp(entry, sl_level, direction):
    risk = abs(entry - sl_level)
    if risk == 0:
        return None, None, 0
    tp = entry + 1.75 * risk if direction == "LONG" else entry - 1.75 * risk
    return round(sl_level, 2), round(tp, 2), risk

def compute_position_size(risk_per_unit, risk_limit=50):
    if risk_per_unit == 0:
        return 0
    return round(risk_limit / risk_per_unit, 1)

def execute_trade(instrument: str, entry_price, sl_price, tp_price, units, direction):
    u = -units if direction == "SHORT" else units
    oanda_service.create_order(
        instrument=instrument,
        entry_price=entry_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        units=u
    )
    return u
