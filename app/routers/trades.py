from fastapi import APIRouter
from app.services.firebase import get_firestore
from app.services import oanda_service
from app.services.log_service import log_to_firestore
from datetime import datetime, timedelta

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()

    # 1. Tous les trades classiques
    classic_trades_snap = db.collection_group("trades").stream()
    classic_trades = [doc.to_dict() | {"id": doc.id} for doc in classic_trades_snap]

    # 2. Tous les trades GPT (sous-collections `executions`)
    executions_snap = db.collection_group("executions").stream()
    executions = []
    for doc in executions_snap:
        data = doc.to_dict()
        strategy = data.get("strategy") or data.get("meta", {}).get("strategy") or "gpt_trader"
        executions.append({
            "id": doc.id,
            "strategy": strategy,
            "direction": data.get("direction"),
            "entry": data.get("entry"),
            "sl": data.get("sl"),
            "tp": data.get("tp"),
            "units": data.get("units"),
            "timestamp": data.get("timestamp"),
            "outcome": data.get("outcome", "unknown"),
            "meta": data.get("meta", {}),
            "oanda_trade_id": data.get("oanda_trade_id"),
            "fill_price": data.get("fill_price"),
            "realized_pnl": data.get("realized_pnl"),
            "close_time": data.get("close_time"),
        })

    all_trades = classic_trades + executions
    all_trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return all_trades


@router.post("/trades/backfill")
def backfill_outcomes():
    """
    One-shot: match Firestore trades (outcome=unknown) with OANDA closed trades
    by comparing entry price, direction, and timestamp proximity.
    """
    db = get_firestore()

    # 1. Fetch all closed trades from OANDA (up to 500)
    try:
        oanda_closed = oanda_service.get_closed_trades(500)
    except Exception as e:
        return {"error": f"OANDA fetch failed: {e}"}

    # Index OANDA trades by (instrument, direction, open_price_rounded)
    # OANDA openTime example: "2024-12-03T15:45:02.123456789Z"
    oanda_index = []
    for ot in oanda_closed:
        units = float(ot.get("initialUnits", 0))
        direction = "LONG" if units > 0 else "SHORT"
        price = float(ot.get("price", 0))
        open_time = ot.get("openTime", "")[:19]  # "2024-12-03T15:45:02"
        try:
            open_dt = datetime.fromisoformat(open_time)
        except Exception:
            open_dt = None
        oanda_index.append({
            "id": ot["id"],
            "instrument": ot.get("instrument", ""),
            "direction": direction,
            "price": price,
            "open_dt": open_dt,
            "realizedPL": float(ot.get("realizedPL", 0)),
            "closeTime": ot.get("closeTime", ""),
            "matched": False,
        })

    # 2. Load ALL trades from Firestore (regardless of current outcome)
    all_docs = []
    for doc in db.collection_group("trades").stream():
        data = doc.to_dict()
        if data.get("entry") and data.get("direction"):
            all_docs.append((doc.reference, data))
    for doc in db.collection_group("executions").stream():
        data = doc.to_dict()
        if data.get("entry") and data.get("direction"):
            all_docs.append((doc.reference, data))

    # 3. Match each Firestore trade to an OANDA trade
    matched = 0
    unmatched = 0
    for doc_ref, data in all_docs:
        entry = float(data.get("entry", 0))
        direction = data.get("direction", "")
        ts = data.get("timestamp", "")
        try:
            fs_dt = datetime.fromisoformat(ts)
        except Exception:
            fs_dt = None

        best = None
        best_score = float("inf")

        for ot in oanda_index:
            if ot["matched"]:
                continue
            if ot["direction"] != direction:
                continue

            # Price proximity (within 5 points)
            price_diff = abs(ot["price"] - entry)
            if price_diff > 5:
                continue

            # Time proximity (within 2 minutes)
            if fs_dt and ot["open_dt"]:
                time_diff = abs((fs_dt - ot["open_dt"]).total_seconds())
                if time_diff > 120:
                    continue
                score = price_diff + time_diff / 60
            else:
                score = price_diff

            if score < best_score:
                best_score = score
                best = ot

        if best:
            best["matched"] = True
            realized_pl = best["realizedPL"]
            if realized_pl > 0:
                outcome = "win"
            elif realized_pl < 0:
                outcome = "loss"
            else:
                outcome = "breakeven"

            doc_ref.update({
                "outcome": outcome,
                "oanda_trade_id": best["id"],
                "fill_price": best["price"],
                "realized_pnl": realized_pl,
                "close_time": best["closeTime"],
            })
            matched += 1
        else:
            unmatched += 1

    log_to_firestore(
        f"[Backfill] Done: {matched} matched, {unmatched} unmatched out of {len(all_docs)} trades",
        level="INFO"
    )
    return {
        "matched": matched,
        "unmatched": unmatched,
        "total_trades": len(all_docs),
        "oanda_closed_count": len(oanda_closed),
    }
