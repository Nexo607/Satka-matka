from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

@dataclass(frozen=True)
class Market:
    name: str
    url: str


MARKETS: dict[str, Market] = {
    "kalyan": Market(
        name="Kalyan",
        url="https://dpbossss.boston/panel-chart-record/kalyan.php",
    ),
    "main-bazar": Market(
        name="Main Bazar",
        url="https://dpbossss.boston/panel-chart-record/main-bazar.php",
    ),
}


HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


THREE_DIGIT_RE = re.compile(r"(?<!\d)\d{3}(?!\d)")


# ------------------------------------------------------------
# Source fetching
# ------------------------------------------------------------

def fetch_source(market: Market) -> tuple[str, int]:
    response = requests.get(
        market.url,
        headers=HTTP_HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError("The source returned an empty response.")

    return response.text, response.status_code


# ------------------------------------------------------------
# Parsing
# ------------------------------------------------------------

def extract_values(html: str) -> tuple[list[str], int]:
    soup = BeautifulSoup(html, "html.parser")

    values: list[str] = []

    # Prefer table cells because they preserve the structure
    # of historical chart pages better than whole-page parsing.
    for cell in soup.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)

        matches = THREE_DIGIT_RE.findall(text)
        values.extend(matches)

    table_count = len(soup.find_all("tr"))

    # Fallback for pages whose historical values are not inside
    # conventional HTML table cells.
    if not values:
        text = soup.get_text(" ", strip=True)
        values = THREE_DIGIT_RE.findall(text)

    return values, table_count


# ------------------------------------------------------------
# Statistical analysis
# ------------------------------------------------------------

def calculate_frequency(values: list[str]) -> list[dict[str, Any]]:
    counter = Counter(values)

    return [
        {
            "value": value,
            "count": count,
        }
        for value, count in counter.most_common()
    ]


def calculate_digit_distribution(
    values: list[str],
) -> list[dict[str, Any]]:

    counter = Counter()

    for value in values:
        counter.update(value)

    return [
        {
            "digit": digit,
            "count": counter[digit],
        }
        for digit in "0123456789"
    ]


def calculate_gap_statistics(
    values: list[str],
) -> list[dict[str, Any]]:

    positions: dict[str, list[int]] = {}

    for index, value in enumerate(values):
        positions.setdefault(value, []).append(index)

    results = []

    for value, indexes in positions.items():

        gaps = [
            indexes[i] - indexes[i - 1]
            for i in range(1, len(indexes))
        ]

        last_seen = len(values) - 1 - indexes[-1]

        average_gap = (
            sum(gaps) / len(gaps)
            if gaps
            else None
        )

        results.append(
            {
                "value": value,
                "occurrences": len(indexes),
                "last_seen_records_ago": last_seen,
                "average_gap": (
                    round(average_gap, 2)
                    if average_gap is not None
                    else None
                ),
            }
        )

    results.sort(
        key=lambda item: (
            -item["occurrences"],
            item["last_seen_records_ago"],
        )
    )

    return results[:50]


def analyze(values: list[str]) -> dict[str, Any]:

    frequency = calculate_frequency(values)

    digit_distribution = calculate_digit_distribution(values)

    gaps = calculate_gap_statistics(values)

    return {
        "records": len(values),
        "unique_values": len(set(values)),
        "frequency": frequency[:50],
        "top_panels": frequency[:20],
        "digit_frequency": digit_distribution,
        "gap_statistics": gaps,
    }


# ------------------------------------------------------------
# API
# ------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "NEXO Historical Analytics",
            "status": "online",
        }
    )


@app.get("/api/markets")
def get_markets():

    return jsonify(
        {
            "ok": True,
            "markets": {
                key: {
                    "name": market.name,
                    "url": market.url,
                }
                for key, market in MARKETS.items()
            },
        }
    )


@app.get("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan",
        type=str,
    ).strip().lower()

    market = MARKETS.get(market_key)

    if market is None:
        return jsonify(
            {
                "ok": False,
                "error": "Unsupported market.",
            }
        ), 400

    try:

        html, http_status = fetch_source(market)

        values, table_rows = extract_values(html)

        analysis = analyze(values)

        return jsonify(
            {
                "ok": True,
                "market": {
                    "key": market_key,
                    "name": market.name,
                    "url": market.url,
                },
                "source": {
                    "http_status": http_status,
                    "html_size": len(html),
                    "table_rows": table_rows,
                },
                "analysis": analysis,
            }
        )

    except requests.Timeout:

        return jsonify(
            {
                "ok": False,
                "error": "The source server timed out.",
            }
        ), 504

    except requests.HTTPError as exc:

        return jsonify(
            {
                "ok": False,
                "error": f"Source HTTP error: {exc}",
            }
        ), 502

    except requests.RequestException as exc:

        return jsonify(
            {
                "ok": False,
                "error": f"Source connection error: {exc}",
            }
        ), 502

    except Exception as exc:

        app.logger.exception("Synchronization failure")

        return jsonify(
            {
                "ok": False,
                "error": f"Analysis error: {exc}",
            }
        ), 500


# ------------------------------------------------------------
# Application entry point
# ------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False,
    )
