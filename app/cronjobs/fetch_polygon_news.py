import requests
import os
from datetime import datetime, timedelta, timezone
from app.services.firebase import get_firestore

API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io/v2/reference/news"

MEGA_CAP_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "BRK.B", "AVGO", "JPM"
}
KEYWORDS = ["S&P", "SP500", "market", "inflation", "rate", "Fed", "Powell", "FOMC", "NASDAQ"]

def fetch_and_store_news():
    db = get_firestore()
    now_utc = datetime.now(timezone.utc)
    start_time = now_utc - timedelta(minutes=30)
    start_time_str = start_time.isoformat().replace("+00:00", "Z")

    params = {
        "order": "desc",
        "limit": 100,
        "sort": "published_utc",
        "published_utc": f">{start_time_str}",
        "apiKey": API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        news_items = response.json().get("results", [])

        for item in news_items:
            news_id = item.get("id")
            title = item.get("title", "")
            description = item.get("description", "")

            # 🔍 Filtrage : contenu lié au marché ou grandes entreprises
            relevant_tickers = list(set(item.get("tickers", [])) & MEGA_CAP_TICKERS)
            if not relevant_tickers and not any(kw.lower() in (title + description).lower() for kw in KEYWORDS):
                continue

            # 🔁 Évite les doublons
            doc_ref = db.collection("polygon_news").document(news_id)
            if doc_ref.get().exists:
                continue

            # 🗃️ Stockage
            doc_ref.set({
                "id": news_id,
                "title": title,
                "summary": description,
                "tickers": relevant_tickers,
                "published_utc": item.get("published_utc"),
                "url": item.get("article_url"),
                "source": item.get("publisher", {}).get("name"),
                "raw": item,
                "inserted_at": now_utc.isoformat(),
                "tags": [],
                "type": None,
                "sentiment": item.get("insights", []),
                "processed_by_gpt": False,
                "alert_sent": False,
            })

    except Exception as e:
        print(f"❌ Erreur récupération news Polygon.io : {e}")

if __name__ == "__main__":
    fetch_and_store_news()
