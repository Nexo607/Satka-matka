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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


def extract_panels(html):
    """
    Extract exact 3-digit values from the source page.
    """

    soup = BeautifulSoup(html, "html.parser")

    values = []

    # First try table cells.
    for cell in soup.find_all(["td", "th"]):

        text = cell.get_text(
            " ",
            strip=True
        )

        # Exact 3-digit value.
        matches = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            text
        )

        values.extend(matches)

    # Fallback to complete page text.
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

    counter = Counter(values)

    digit_counter = Counter()

    for value in values:
        for digit in value:
            digit_counter[digit] += 1

    top_panels = [
        {
            "value": value,
            "count": count
        }
        for value, count in counter.most_common(50)
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
        "unique": len(counter),
        "top_panels": top_panels,
        "digit_frequency": digit_frequency
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "service": "NEXO",
        "status": "online"
    })


@app.route("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan"
    )

    selected_date = request.args.get(
        "date",
        ""
    )

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Invalid market."
        }), 400

    market = MARKETS[market_key]

    try:

        response = requests.get(
            market["url"],
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        html = response.text

        values = extract_panels(html)

        analysis = analyze(values)

        return jsonify({

            "ok": True,

            "market": market["name"],

            "market_key": market_key,

            "requested_date": selected_date,

            "source": market["url"],

            "http_status": response.status_code,

            "content_type":
                response.headers.get(
                    "content-type",
                    ""
                ),

            "html_size": len(html),

            "rows_found":
                len(
                    BeautifulSoup(
                        html,
                        "html.parser"
                    ).find_all("tr")
                ),

            "analysis": analysis

        })

    except requests.exceptions.Timeout:

        return jsonify({
            "ok": False,
            "error": "Source website timed out."
        }), 504

    except requests.exceptions.RequestException as error:

        return jsonify({
            "ok": False,
            "error": f"Source request failed: {error}"
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
