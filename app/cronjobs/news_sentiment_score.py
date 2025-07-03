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

def fetch_news_summaries():
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=6)  # on lit les news des dernières heures
    query = db.collection("all_news") \
        .where("impact_score", ">=", 0.6) \
        .where("type", "in", ["macro", "breaking"]) \
        .where("fetched_at", ">=", since.isoformat())

    news = [n.to_dict() for n in query.stream()]
    return [f"Titre: {n['title']}\nRésumé: {n.get('summary', '')}" for n in news if 'title' in n]

def update_sentiment_score():
    # Vérifie si on est entre 09:00 et 12:00 NY
    ny_time = datetime.now(pytz.utc).astimezone(pytz.timezone("America/New_York")).time()
    if not (datetime.strptime("09:00", "%H:%M").time() <= ny_time <= datetime.strptime("12:00", "%H:%M").time()):
        print(f"⏱️ Hors plage horaire NY (actuel : {ny_time}) → skipping")
        return

    summaries = fetch_news_summaries()
    if not summaries:
        print("⛔ Aucune news à analyser.")
        return

    prompt = "Voici les news importantes du jour :\n\n" + "\n\n".join(summaries)

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
        timestamp = datetime.now(timezone.utc).isoformat()

        db.collection("news_sentiment_score").add({
            "timestamp": timestamp,
            "note": note,
            "justification": result.get("justification", "")
        })
        print(f"✅ Note news enregistrée : {note}")

    except Exception as e:
        print(f"❌ Erreur GPT ou Firestore : {e}")

if __name__ == "__main__":
    update_sentiment_score()
