# tools/list_polygon_indices.py
import os, requests

API = "https://api.polygon.io/v3/reference/tickers"
KEY = "nofu8iTGCUXDvjbrh30FBLNWQQ06j4wk"

def fetch_all_indices():
    url = f"{API}?market=indices&active=true&limit=1000&sort=ticker&order=asc&apiKey={KEY}"
    while url:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        for row in data.get("results", []):
            yield row
        url = data.get("next_url")
        if url:
            url += f"&apiKey={KEY}"

if __name__ == "__main__":
    want = ["stoxx", "sx5e", "euro", "cac", "px1", "ftse", "ukx", "smi", "swiss"]
    results = list(fetch_all_indices())
    print(f"Total indices visible with this key: {len(results)}")
    hits = [r for r in results if any(k in (r.get("ticker","")+ " " + r.get("name","")).lower() for k in want)]
    for r in hits:
        print(f"{r['ticker']:>10}  |  {r.get('name','')}")
