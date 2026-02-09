from fastapi import APIRouter, Query
from app.services.firebase import get_firestore

router = APIRouter()

@router.get("/logs")
def get_logs(
    limit: int = 50,
    level: str = Query(None),
    contains: str = Query(None),
    tag: str = Query(None),
    trade_id: str = Query(None),
):
    db = get_firestore()

    # Extend search volume when filtering client-side
    needs_filter = contains or tag or trade_id
    search_limit = 1000 if needs_filter else limit

    query = db.collection("execution_logs")

    if level:
        query = query.where("level", "==", level.upper())

    query = query.order_by("timestamp", direction="DESCENDING")

    docs = query.limit(search_limit).stream()
    results = []

    for doc in docs:
        data = doc.to_dict()

        # Filter by strategy/service tag
        if tag and data.get("tag", "").lower() != tag.lower():
            continue

        # Filter by oanda trade ID (search in message text)
        if trade_id and trade_id not in data.get("message", ""):
            continue

        # Multi-keyword text search
        if contains:
            keywords = contains.lower().split()
            message = data.get("message", "").lower()
            timestamp = data.get("timestamp", "").lower()
            lvl = data.get("level", "").lower()

            if not all(
                any(kw in field for field in [message, timestamp, lvl])
                for kw in keywords
            ):
                continue

        results.append(data)

        if len(results) >= limit:
            break

    return results


@router.get("/logs/tags")
def get_log_tags():
    """Return distinct tags from recent logs."""
    db = get_firestore()
    docs = db.collection("execution_logs").order_by("timestamp", direction="DESCENDING").limit(500).stream()
    tags = set()
    for doc in docs:
        t = doc.to_dict().get("tag")
        if t:
            tags.add(t)
    return sorted(tags)
