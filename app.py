from flask import Flask, jsonify, render_template, request
import requests
from bs4 import BeautifulSoup
from collections import Counter
import re

app = Flask(__name__)

MARKETS = {
    "kalyan-main-bazar": {
        "name": "Kalyan Main Bazar",
        "jodi": "https://dpbossss.boston/jodi-chart-record/kalyan-main-bazar.php",
        "panel": "https://dpbossss.boston/panel-chart-record/kalyan-main-bazar.php",
    },
    "main-bazar": {
        "name": "Main Bazar",
        "jodi": "https://dpbossss.boston/jodi-chart-record/main-bazar.php",
        "panel": "https://dpbossss.boston/panel-chart-record/main-bazar.php",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    )
}

TIMEOUT = 20


def fetch(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()
    return response.text


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser")

    rows = []

    for tr in soup.find_all("tr"):
        cells = [
            cell.get_text(" ", strip=True)
            for cell in tr.find_all(["th", "td"])
        ]

        if cells:
            rows.append(cells)

    text = soup.get_text(" ", strip=True)

    # Three-digit values only.
    panels = re.findall(r"(?<!\d)\d{3}(?!\d)", text)

    # Two-digit values.
    jodis = re.findall(r"(?<!\d)\d{2}(?!\d)", text)

    return {
        "rows": rows,
        "panels": panels,
        "jodis": jodis
    }


def frequency(values):
    counter = Counter(values)

    return [
        {
            "value": value,
            "count": count
        }
        for value, count in counter.most_common(30)
    ]


def digit_frequency(values):
    counter = Counter("".join(values))

    return [
        {
            "digit": digit,
            "count": counter[digit]
        }
        for digit in "0123456789"
    ]


def gap_analysis(values):
    positions = {}
    gaps = {}

    for index, value in enumerate(values):
        if value in positions:
            gap = index - positions[value] - 1
            gaps[value] = gap

        positions[value] = index

    result = []

    for value, gap in sorted(
        gaps.items(),
        key=lambda x: x[1],
        reverse=True
    )[:30]:

        result.append({
            "value": value,
            "gap": gap
        })

    return result


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/markets")
def markets():
    return jsonify([
        {
            "id": key,
            "name": value["name"]
        }
        for key, value in MARKETS.items()
    ])


@app.route("/api/sync")
def sync():
    market_id = request.args.get(
        "market",
        "kalyan-main-bazar"
    )

    if market_id not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 400

    market = MARKETS[market_id]

    try:
        jodi_html = fetch(market["jodi"])
        panel_html = fetch(market["panel"])

        jodi_data = parse_page(jodi_html)
        panel_data = parse_page(panel_html)

        # Panel page is the primary source for 3-digit historical values.
        panels = panel_data["panels"]

        # Jodi page gives 2-digit historical values.
        jodis = jodi_data["jodis"]

        result = {
            "ok": True,
            "market": market["name"],

            "sources": {
                "jodi": market["jodi"],
                "panel": market["panel"]
            },

            "records": {
                "jodi": len(jodis),
                "panel": len(panels)
            },

            "jodi_frequency": frequency(jodis),

            "panel_frequency": frequency(panels),

            "jodi_digit_frequency": digit_frequency(jodis),

            "panel_digit_frequency": digit_frequency(panels),

            "jodi_gaps": gap_analysis(jodis),

            "panel_gaps": gap_analysis(panels),

            "latest_jodi": jodis[:50],

            "latest_panel": panels[:50]
        }

        return jsonify(result)

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 502


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
