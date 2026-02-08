from fastapi import APIRouter
from google.cloud.firestore_v1 import FieldFilter
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/trades")
def get_all_trades():
    db = get_firestore()

    trades_snap = db.collection_group("trades").stream()
    trades = []
    for doc in trades_snap:
        data = doc.to_dict()
        if not data.get("entry"):
            continue
        trades.append(data | {"id": doc.id})

    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    return trades


@router.get("/trades/stats")
def get_trade_stats():
    db = get_firestore()
    trades_snap = db.collection_group("trades").stream()

    stats = {}
    for doc in trades_snap:
        t = doc.to_dict()
        if not t.get("entry") or not t.get("strategy"):
            continue
        strat = t["strategy"]
        if strat not in stats:
            stats[strat] = {"trades": [], "name": strat}
        stats[strat]["trades"].append(t)

    result = {}
    for strat, data in stats.items():
        trades = data["trades"]
        closed = [t for t in trades if t.get("outcome") not in (None, "open")]
        wins = [t for t in closed if t.get("outcome") == "win"]
        losses = [t for t in closed if t.get("outcome") == "loss"]
        breakevens = [t for t in closed if t.get("outcome") == "breakeven"]

        pnls = [t.get("realized_pnl", 0) for t in closed if t.get("realized_pnl") is not None]
        total_pnl = sum(pnls)
        win_pnls = [p for p in pnls if p > 0]
        loss_pnls = [p for p in pnls if p < 0]

        result[strat] = {
            "total_trades": len(trades),
            "closed_trades": len(closed),
            "open_trades": len(trades) - len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
            "avg_loss": round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "profit_factor": round(sum(win_pnls) / abs(sum(loss_pnls)), 2) if loss_pnls and sum(loss_pnls) != 0 else None,
            "pnl_history": sorted(
                [{"date": t.get("date"), "pnl": t.get("realized_pnl", 0)} for t in closed if t.get("realized_pnl") is not None],
                key=lambda x: x["date"] or ""
            ),
        }

    all_pnls = [v["total_pnl"] for v in result.values()]
    return {
        "strategies": result,
        "global_pnl": round(sum(all_pnls), 2),
    }


@router.get("/trades/{oanda_trade_id}/events")
def get_trade_events(oanda_trade_id: str):
    db = get_firestore()

    # Find the trade doc by oanda_trade_id
    docs = db.collection_group("trades").where(
        filter=FieldFilter("oanda_trade_id", "==", oanda_trade_id)
    ).stream()

    trade_doc = None
    for doc in docs:
        if doc.to_dict().get("entry"):
            trade_doc = doc
            break

    if not trade_doc:
        return []

    # Get events subcollection ordered by timestamp
    events = trade_doc.reference.collection("events").order_by("timestamp").stream()
    return [e.to_dict() for e in events]
