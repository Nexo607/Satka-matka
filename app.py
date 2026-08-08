from flask import Flask, jsonify, request, render_template
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
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9"
}


def extract_three_digit_values(html):
    soup = BeautifulSoup(html, "html.parser")

    values = []

    # First: table cells
    for cell in soup.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)

        matches = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            text
        )

        values.extend(matches)

    # Fallback: entire document
    if not values:
        text = soup.get_text(" ", strip=True)

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
        for value, count in panel_counter.most_common(50)
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
        "service": "NEXO Market Analytics",
        "status": "online"
    })


@app.route("/api/markets")
def markets():
    return jsonify({
        "ok": True,
        "markets": MARKETS
    })


@app.route("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan"
    ).strip().lower()

    selected_date = request.args.get(
        "date",
        ""
    ).strip()

    if market_key not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Invalid market selected."
        }), 400

    market = MARKETS[market_key]

    try:

        response = requests.get(
            market["url"],
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        response.raise_for_status()

        html = response.text

        if not html:
            return jsonify({
                "ok": False,
                "error": "Source returned empty HTML."
            }), 502

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        values = extract_three_digit_values(html)

        analysis = analyze(values)

        table_rows = len(
            soup.find_all("tr")
        )

        return jsonify({

            "ok": True,

            "market": market["name"],

            "market_key": market_key,

            "requested_date": selected_date,

            "source": market["url"],

            "http_status":
                response.status_code,

            "content_type":
                response.headers.get(
                    "content-type",
                    ""
                ),

            "html_size":
                len(html),

            "table_rows":
                table_rows,

            "rows_found":
                len(values),

            "analysis":
                analysis

        })

    except requests.exceptions.Timeout:

        return jsonify({
            "ok": False,
            "error": "Source website timed out."
        }), 504

    except requests.exceptions.HTTPError as error:

        return jsonify({
            "ok": False,
            "error": f"HTTP error: {error}"
        }), 502

    except requests.exceptions.RequestException as error:

        return jsonify({
            "ok": False,
            "error": f"Connection error: {error}"
        }), 502

    except Exception as error:

        return jsonify({
            "ok": False,
            "error": f"Parser error: {error}"
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
