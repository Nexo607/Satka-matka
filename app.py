from flask import Flask, jsonify, render_template
import requests
from bs4 import BeautifulSoup
from collections import Counter
import re

app = Flask(__name__)

MARKETS = {
    "kalyan": {
        "name": "Kalyan",
        "url": "https://dpbossss.boston/panel-chart-record/kalyan.php"
    },
    "main-bazar": {
        "name": "Main Bazar",
        "url": "https://dpbossss.boston/panel-chart-record/main-bazar.php"
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def extract_values(html):

    soup = BeautifulSoup(html, "html.parser")

    values = []

    # Read table cells first
    for cell in soup.find_all(["td", "th"]):

        text = cell.get_text(
            " ",
            strip=True
        )

        matches = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            text
        )

        values.extend(matches)

    # Fallback to entire page
    if not values:

        text = soup.get_text(
            " ",
            strip=True
        )

        values = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            text
        )

    return values


def analyze(values):

    panel_counter = Counter(values)
    digit_counter = Counter()

    for value in values:

        for digit in value:
            digit_counter[digit] += 1

    top_panels = [
        {
            "value": value,
            "count": count
        }
        for value, count
        in panel_counter.most_common(50)
    ]

    digit_frequency = [
        {
            "digit": digit,
            "count": digit_counter[digit]
        }
        for digit in "0123456789"
    ]

    return {
        "records": len(values),
        "unique": len(panel_counter),
        "top_panels": top_panels,
        "digit_frequency": digit_frequency
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "status": "online"
    })


@app.route("/api/sync")
def sync():

    market_key = "kalyan"

    # The frontend sends the selected market
    from flask import request

    market_key = request.args.get(
        "market",
        "kalyan"
    )

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Invalid market"
        }), 400

    market = MARKETS[market_key]

    try:

        response = requests.get(
            market["url"],
            headers=HEADERS,
            timeout=30
        )

        response.raise_for_status()

        html = response.text

        values = extract_values(html)

        analysis = analyze(values)

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        return jsonify({

            "ok": True,

            "market": market["name"],

            "source": market["url"],

            "http_status":
                response.status_code,

            "html_size":
                len(html),

            "table_rows":
                len(soup.find_all("tr")),

            "rows_found":
                len(values),

            "analysis":
                analysis
        })

    except requests.exceptions.Timeout:

        return jsonify({
            "ok": False,
            "error": "Source website timed out"
        }), 504

    except requests.exceptions.RequestException as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 502

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
