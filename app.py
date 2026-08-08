from flask import Flask, jsonify, render_template, request
import requests, re, statistics
from bs4 import BeautifulSoup
from collections import Counter

app = Flask(__name__)

SOURCE_URL = "https://dpbossss.boston/"
TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; NexoAnalytics/1.0)"

def fetch_source(url=SOURCE_URL):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text, r.url

def extract_data(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    # Extract visible table rows.
    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.select("th,td")]
        if cells:
            rows.append(cells)

    # Extract three-digit tokens from visible text. This is deliberately
    # conservative: it does not invent or predict missing results.
    text = soup.get_text(" ", strip=True)
    tokens = re.findall(r"(?<!\d)\d{3}(?!\d)", text)
    return rows, tokens

def analyze(tokens):
    freq = Counter(tokens)
    ordered = list(dict.fromkeys(tokens))
    gaps = {}
    last = {}
    for i, value in enumerate(tokens):
        if value in last:
            gaps[value] = i - last[value] - 1
        last[value] = i

    digit_freq = Counter("".join(tokens))
    top = [{"value": v, "count": c} for v, c in freq.most_common(20)]
    digit = [{"digit": d, "count": digit_freq[d]} for d in "0123456789"]

    return {
        "records": len(tokens),
        "unique": len(freq),
        "top_panels": top,
        "digit_frequency": digit,
        "latest_values": tokens[:50],
        "gap_examples": [
            {"value": v, "gap": g} for v, g in sorted(gaps.items(), key=lambda x: -x[1])[:20]
        ],
        "note": "These are historical descriptive statistics only; they do not establish or guarantee a future result."
    }

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/sync")
def sync():
    try:
        html, final_url = fetch_source(request.args.get("url", SOURCE_URL))
        rows, tokens = extract_data(html)
        return jsonify({
            "ok": True,
            "source": final_url,
            "rows_found": len(rows),
            "analysis": analyze(tokens)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
