import requests
import os
import json
from datetime import datetime, timedelta, timezone
from app.services.firebase import get_firestore
 

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
POLYGON_NEWS_URL = "https://api.polygon.io/v2/reference/news"

MEGA_CAP_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "BRK.B", "AVGO", "JPM"
}

def fetch_and_store_news():
    db = get_firestore()
    now_utc = datetime.now(timezone.utc)
    start_time = now_utc - timedelta(minutes=30)
    start_time_str = start_time.isoformat()

    params = {
        "order": "desc",
        "limit": 100,
        "sort": "published_utc",
        "published_utc.gte": start_time_str,
        "apiKey": POLYGON_API_KEY
    }

    try:
        response = requests.get(POLYGON_NEWS_URL, params=params)
        response.raise_for_status()
        articles = response.json().get("results", [])

        for news in articles:
            news_id = news.get("id")
            tickers = set(news.get("tickers", []))
            mega_tickers = list(tickers & MEGA_CAP_TICKERS)

            if not mega_tickers:
                continue  # Ignore non-mega cap

            doc_ref = db.collection("polygon_news").document(news_id)
            if doc_ref.get().exists:
                continue

            doc_ref.set({
                "id": news_id,
                "title": news.get("title"),
                "description": news.get("description"),
                "tickers": mega_tickers,
                "published_utc": news.get("published_utc"),
                "url": news.get("article_url"),
                "source": news.get("publisher", {}).get("name"),
                "raw": news,
                "inserted_at": now_utc.isoformat(),
                "tags": [],
                "type": None,
                "sentiment": news.get("insights", []),
                "processed_by_gpt": False,
                "alert_sent": False,
            })

    except Exception as e:
        print(f"❌ Erreur récupération news Polygon.io : {e}")

if __name__ == "__main__":
    fetch_and_store_news()
