import os, json, feedparser, hashlib
from datetime import datetime, timezone
from openai import OpenAI
from app.services.firebase import get_firestore
from dotenv import load_dotenv
load_dotenv()

RSS_URL = "https://www.cnbc.com/id/100003114/device/rss/rss.html"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
db = get_firestore()

SYSTEM_PROMPT = """
Tu es un analyste financier. À partir d’un titre et d’un résumé, extrais :
- tags : liste de mots-clés
- type : "macro", "company", "breaking" ou "other"
- impact_score : float entre 0 et 1
- summary : résumé rapide

Réponds uniquement en JSON.
"""

def fetch_and_store_rss():
    feed = feedparser.parse(RSS_URL)
    for entry in feed.entries:
        title = entry.title
        uid = "rss_" + hashlib.sha1(title.encode()).hexdigest()
        doc = db.collection("all_news").document(uid)
        if doc.get().exists:
            continue

        # Récupère le résumé si disponible, sinon chaîne vide
        summary = getattr(entry, "summary", "").strip()
        if not summary:
            print(f"⚠️ Pas de résumé pour: {title[:60]} — ignoré")
            continue  # ou tu peux décider de traiter quand même

        try:
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT.strip()},
                    {"role": "user", "content": f"Titre: {title}\nRésumé: {summary}"}
                ],
                temperature=0.2
            )
            content = completion.choices[0].message.content.strip()
            data = json.loads(content)

            doc.set({
                "source": "rss_cnbc",
                "title": title,
                "description": summary,
                "url": entry.link,
                "published_utc": entry.published,
                "tags": data.get("tags", []),
                "type": data.get("type"),
                "impact_score": data.get("impact_score"),
                "summary": data.get("summary"),
                "processed_by_gpt": True,
                "fetched_at": datetime.now(timezone.utc).isoformat()
            })
            print(f"✅ Saved: {title[:60]}...")
        except Exception as e:
            print(f"❌ Error processing: {title[:60]} — {e}")


if __name__ == "__main__":
    fetch_and_store_rss()
