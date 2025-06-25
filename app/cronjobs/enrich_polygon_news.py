import os
import json
import openai
from datetime import datetime
from app.services.firebase import get_firestore

openai.api_key = os.getenv("OPENAI_API_KEY")
db = get_firestore()

SYSTEM_PROMPT = """
Tu es un analyste financier. À partir d’un titre et d’un résumé de news, tu dois extraire :

- "tags" : liste de mots-clés (ex : ["macro", "inflation", "Fed", "earnings"])
- "type" : soit "macro", "company", "sentiment", "breaking"
- "impact_score" : un float entre 0 et 1 (impact potentiel sur le marché à court terme)
- "summary" : un résumé simplifié

Réponds toujours en JSON pur.
"""

def enrich_news_with_gpt():
    docs = db.collection("polygon_news").where("processed_by_gpt", "==", False).limit(10).stream()

    for doc in docs:
        news = doc.to_dict()
        prompt = f"Titre: {news.get('title')}\nDescription: {news.get('description')}"

        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT.strip()},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )
            result = response.choices[0].message.content.strip()
            gpt_data = json.loads(result)

            doc.reference.update({
                "tags": gpt_data.get("tags", []),
                "type": gpt_data.get("type"),
                "impact_score": gpt_data.get("impact_score"),
                "summary": gpt_data.get("summary"),
                "processed_by_gpt": True,
                "processed_at": datetime.utcnow().isoformat()
            })

            print(f"✅ Enrichie : {news.get('title')[:60]}...")

        except Exception as e:
            print(f"❌ GPT erreur sur {news.get('title')[:60]}... : {e}")

if __name__ == "__main__":
    enrich_news_with_gpt()
