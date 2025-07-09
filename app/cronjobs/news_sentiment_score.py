import os
import json
from datetime import datetime, timezone, timedelta
import pytz
from openai import OpenAI
from app.services.firebase import get_firestore

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
db = get_firestore()

SYSTEM_PROMPT = """
Tu es un analyste financier. Reçois une liste de news importantes du jour (titre + résumé), et attribue une note globale d'impact marché.

Réponds uniquement en JSON :
{
  "note": int (entre 0 = très baissier, 100 = très haussier),
  "justification": "..."
}
"""

def get_last_sentiment_data():
    # Récupère la dernière note
    query = db.collection("news_sentiment_score").order_by("timestamp", direction="DESCENDING").limit(1).stream()
    for doc in query:
        data = doc.to_dict()
        return {
            "timestamp": datetime.fromisoformat(data["timestamp"]),
            "last_news_title": data.get("last_news_title", None)
        }
    return {"timestamp": datetime.now(timezone.utc) - timedelta(hours=6), "last_news_title": None}


def fetch_news_summaries(since):
    query = db.collection("all_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("fetched_at", ">=", since.isoformat()) \
        .order_by("fetched_at", direction="DESCENDING")

    news = [n.to_dict() for n in query.stream()]
    return news


def update_sentiment_score():
    # Vérifie si on est entre 09:00 et 12:00 NY
    ny_time = datetime.now(pytz.utc).astimezone(pytz.timezone("America/New_York")).time()
    if not (datetime.strptime("09:00", "%H:%M").time() <= ny_time <= datetime.strptime("12:00", "%H:%M").time()):
        print(f"⏱️ Hors plage horaire NY (actuel : {ny_time}) → skipping")
        return

    # Récupère dernière exécution
    last_data = get_last_sentiment_data()
    last_check = last_data["timestamp"]
    last_news_title = last_data["last_news_title"]

    # News importantes depuis la dernière analyse
    news = fetch_news_summaries(since=last_check)

    if not news:
        print(f"📭 Pas de nouvelles news depuis {last_check.isoformat()} → GPT skip")
        return

    latest_title = news[0]["title"]
    if latest_title == last_news_title:
        print(f"♻️ Même dernière news (« {latest_title} ») → skipping GPT")
        return

    # Génère le prompt avec toutes les news
    summaries = [f"Titre: {n['title']}\nRésumé: {n.get('summary', '')}" for n in news if 'title' in n]

    prompt = "Voici les news importantes du jour :\n\n" + "\n\n".join(summaries)
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.strip()},
                {"role": "user", "content": prompt.strip()}
            ],
            temperature=0.2
        )
        result = json.loads(response.choices[0].message.content.strip())
        note = max(0, min(100, int(result.get("note", 50))))

        db.collection("news_sentiment_score").add({
            "timestamp": timestamp,
            "note": note,
            "justification": result.get("justification", ""),
            "last_news_title": latest_title
        })
        print(f"✅ Nouvelle note enregistrée : {note} (news : {latest_title})")

    except Exception as e:
        print(f"❌ Erreur GPT ou Firestore : {e}")


if __name__ == "__main__":
    update_sentiment_score()
