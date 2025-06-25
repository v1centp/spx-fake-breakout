import requests
import os
from datetime import datetime, timedelta
from app.services.firebase import get_firestore  # adapte le chemin si besoin

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
BASE_URL = "https://api.polygon.io/v2/reference/news"
KEYWORDS = ["S&P", "SP500", "market", "inflation", "rate", "Fed", "Powell", "FOMC", "NASDAQ"]

MEGA_CAP_TICKERS = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "BRK.B", "AVGO", "JPM"
}

def fetch_and_store_news():
    db = get_firestore()
    now_utc = datetime.utcnow()
    start_time = now_utc - timedelta(minutes=30)

    params = {
        "order": "desc",
        "limit": 100,
        "sort": "published_utc",
        "published_utc.gte": start_time.isoformat(),
        "apiKey": POLYGON_API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        articles = response.json().get("results", [])

        for news in articles:
            news_id = news.get("id")
            title = news.get("title", "")
            summary = news.get("description", "")  # "summary" may not exist
            tickers = set(news.get("tickers", []))
            keywords_match = any(kw.lower() in (title + summary).lower() for kw in KEYWORDS)
            tickers_match = bool(tickers & MEGA_CAP_TICKERS)

            if not (keywords_match or tickers_match):
                continue

            doc_ref = db.collection("polygon_news").document(news_id)
            if doc_ref.get().exists:
                continue

            doc_ref.set({
                "id": news_id,
                "title": title,
                "summary": summary,
                "tickers": list(tickers),
                "published_utc": news.get("published_utc"),
                "url": news.get("article_url"),
                "source": news.get("publisher", {}).get("name", ""),
                "image_url": news.get("image_url"),
                "sentiment": news.get("insights", []),
                "raw": news,
                "inserted_at": datetime.utcnow().isoformat(),
                "tags": [],
                "type": None,
                "processed_by_gpt": False,
                "alert_sent": False,
            })

    except Exception as e:
        print(f"❌ Erreur récupération news Polygon.io : {e}")

if __name__ == "__main__":
    fetch_and_store_news()
