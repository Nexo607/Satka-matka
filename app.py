import re
import requests

from flask import Flask, jsonify, render_template, request

from database import (
    init_db,
    save_records,
    get_records,
    count_records
)

from analytics import analyze


app = Flask(__name__)

init_db()

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
        "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml"
}


def fetch_page(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=20
    )

    response.raise_for_status()

    if not response.text:
        raise ValueError("Empty response received")

    return response.text


def extract_panels(html):
    """
    Extract three-digit panel values from the panel-chart page.

    The parser intentionally accepts only standalone 3-digit
    numeric tokens to reduce accidental extraction of times,
    dates, IDs, etc.
    """

    soup_text = " ".join(
        html.replace("<", " <")
            .replace(">", "> ")
            .split()
    )

    # Find standalone 3-digit numbers.
    matches = re.findall(
        r"(?<!\d)(\d{3})(?!\d)",
        soup_text
    )

    # Preserve order while removing duplicates.
    result = []
    seen = set()

    for value in matches:
        if value not in seen:
            seen.add(value)
            result.append(value)

    return result


def sync_market(market_key):
    if market_key not in MARKETS:
        raise ValueError("Unknown market")

    market = MARKETS[market_key]

    html = fetch_page(market["url"])

    panels = extract_panels(html)

    if not panels:
        raise ValueError(
            "No 3-digit panel records were found. "
            "The source page may have changed its HTML structure."
        )

    inserted = save_records(
        market["name"],
        panels,
        market["url"]
    )

    records = get_records(market["name"])

    return {
        "market": market["name"],
        "source": market["url"],
        "fetched": len(panels),
        "new_records": inserted,
        "stored_records": len(records),
        "analysis": analyze(records)
    }


@app.route("/")
def home():
    return render_template(
        "index.html",
        markets=MARKETS
    )


@app.route("/api/markets")
def markets():
    return jsonify({
        key: {
            "name": value["name"],
            "url": value["url"],
            "records": count_records(value["name"])
        }
        for key, value in MARKETS.items()
    })


@app.route("/api/sync/<market_key>", methods=["POST"])
def sync(market_key):
    try:
        result = sync_market(market_key)

        return jsonify({
            "ok": True,
            **result
        })

    except requests.RequestException as exc:
        return jsonify({
            "ok": False,
            "error": f"Source request failed: {str(exc)}"
        }), 502

    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


@app.route("/api/analysis/<market_key>")
def analysis(market_key):
    if market_key not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    market_name = MARKETS[market_key]["name"]

    records = get_records(market_name)

    return jsonify({
        "ok": True,
        "market": market_name,
        "source": MARKETS[market_key]["url"],
        "analysis": analyze(records)
    })


@app.route("/api/history/<market_key>")
def history(market_key):
    if market_key not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    market_name = MARKETS[market_key]["name"]

    records = get_records(market_name)

    return jsonify({
        "ok": True,
        "market": market_name,
        "records": records
    })


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
