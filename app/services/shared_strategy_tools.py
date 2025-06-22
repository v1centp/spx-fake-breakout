from app.services import oanda_service
from datetime import datetime
from app.services.firebase import get_firestore

def get_entry_price():
    return oanda_service.get_latest_price("SPX500_USD")

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

def execute_trade(entry_price, sl_price, tp_price, units, direction):
    if direction == "SHORT":
        units = -units
    oanda_service.create_order(
        instrument="SPX500_USD",
        entry_price=entry_price,
        stop_loss_price=sl_price,
        take_profit_price=tp_price,
        units=units
    )
    return units

