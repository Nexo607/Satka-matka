from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Any

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


# ============================================================
# MARKET CONFIGURATION
# ============================================================

@dataclass(frozen=True)
class Market:
    name: str
    url: str


MARKETS = {
    "kalyan": Market(
        "Kalyan",
        "https://dpbossss.boston/panel-chart-record/kalyan.php"
    ),
    "main-bazar": Market(
        "Main Bazar",
        "https://dpbossss.boston/panel-chart-record/main-bazar.php"
    ),
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
}


THREE_DIGIT_RE = re.compile(
    r"(?<!\d)\d{3}(?!\d)"
)


# ============================================================
# SOURCE FETCH
# ============================================================

def fetch_source(market: Market):

    response = requests.get(
        market.url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True
    )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError(
            "Source returned an empty response."
        )

    return response.text, response.status_code


# ============================================================
# PARSER
# ============================================================

def extract_values(html: str):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    values = []

    # Prefer table cells.
    for cell in soup.find_all(
        ["td", "th"]
    ):

        text = cell.get_text(
            " ",
            strip=True
        )

        values.extend(
            THREE_DIGIT_RE.findall(text)
        )

    table_rows = len(
        soup.find_all("tr")
    )

    # Fallback.
    if not values:

        text = soup.get_text(
            " ",
            strip=True
        )

        values = THREE_DIGIT_RE.findall(
            text
        )

    return values, table_rows


# ============================================================
# BASIC STATISTICS
# ============================================================

def frequency(values):

    counter = Counter(values)

    return [
        {
            "value": value,
            "count": count
        }
        for value, count
        in counter.most_common()
    ]


def digit_distribution(values):

    counter = Counter()

    for value in values:
        counter.update(value)

    return [
        {
            "digit": digit,
            "count": counter[digit]
        }
        for digit in "0123456789"
    ]


# ============================================================
# POSITION ANALYSIS
# ============================================================

def position_distribution(values):

    positions = [
        Counter(),
        Counter(),
        Counter()
    ]

    for value in values:

        if len(value) != 3:
            continue

        for index, digit in enumerate(value):
            positions[index][digit] += 1

    result = []

    for index, counter in enumerate(positions):

        result.append({
            "position": index + 1,
            "distribution": [
                {
                    "digit": digit,
                    "count": counter[digit]
                }
                for digit in "0123456789"
            ]
        })

    return result


# ============================================================
# GAP ANALYSIS
# ============================================================

def gap_analysis(values):

    positions = {}

    for index, value in enumerate(values):

        positions.setdefault(
            value,
            []
        ).append(index)

    results = []

    total = len(values)

    for value, indexes in positions.items():

        gaps = [
            indexes[i] - indexes[i - 1]
            for i in range(
                1,
                len(indexes)
            )
        ]

        last_seen = (
            total - 1 - indexes[-1]
        )

        avg_gap = (
            mean(gaps)
            if gaps
            else None
        )

        results.append({

            "value": value,

            "occurrences":
                len(indexes),

            "last_seen":
                last_seen,

            "average_gap":
                round(avg_gap, 2)
                if avg_gap is not None
                else None,

            "min_gap":
                min(gaps)
                if gaps
                else None,

            "max_gap":
                max(gaps)
                if gaps
                else None

        })

    results.sort(
        key=lambda x: (
            -x["occurrences"],
            x["last_seen"]
        )
    )

    return results[:100]


# ============================================================
# RECENT VS HISTORICAL
# ============================================================

def recent_analysis(values):

    if not values:
        return []

    window = min(
        100,
        len(values)
    )

    recent = values[-window:]

    full_counter = Counter(values)
    recent_counter = Counter(recent)

    result = []

    for value, total_count in (
        full_counter.items()
    ):

        recent_count = recent_counter[
            value
        ]

        historical_rate = (
            total_count / len(values)
        )

        recent_rate = (
            recent_count / len(recent)
        )

        change = (
            recent_rate -
            historical_rate
        )

        result.append({

            "value": value,

            "total":
                total_count,

            "recent":
                recent_count,

            "change":
                round(change, 6)

        })

    result.sort(
        key=lambda x: x["change"],
        reverse=True
    )

    return result[:50]


# ============================================================
# CANDIDATE SCORING
# ============================================================

def candidate_scores(values):

    if len(values) < 10:
        return []

    total = len(values)

    recent_window = min(
        100,
        total
    )

    recent = values[-recent_window:]

    full_counter = Counter(values)
    recent_counter = Counter(recent)

    gaps = gap_analysis(values)

    gap_map = {
        item["value"]: item
        for item in gaps
    }

    max_frequency = max(
        full_counter.values()
    )

    max_recent = max(
        recent_counter.values(),
        default=1
    )

    candidates = []

    for value, count in full_counter.items():

        frequency_score = (
            count /
            max_frequency
        ) * 40

        recent_score = (
            recent_counter[value] /
            max_recent
        ) * 30

        gap_info = gap_map.get(
            value
        )

        gap_score = 0

        if gap_info:

            average_gap = (
                gap_info["average_gap"]
            )

            last_seen = (
                gap_info["last_seen"]
            )

            if (
                average_gap is not None
                and average_gap > 0
            ):

                distance_ratio = (
                    last_seen /
                    average_gap
                )

                # Moderate recurrence score.
                # It does not claim a future outcome.
                gap_score = min(
                    distance_ratio,
                    2.0
                ) / 2.0 * 20

        position_score = 0

        # Digit-position historical support.
        for position in range(3):

            digit = value[position]

            position_counter = Counter(
                v[position]
                for v in values
                if len(v) == 3
            )

            position_total = sum(
                position_counter.values()
            )

            if position_total:

                position_score += (
                    position_counter[digit] /
                    position_total
                ) * 10

        score = (
            frequency_score +
            recent_score +
            gap_score +
            position_score
        )

        candidates.append({

            "value": value,

            "score": round(
                min(score, 100),
                2
            ),

            "frequency": count,

            "recent_frequency":
                recent_counter[value],

            "last_seen":
                gap_info["last_seen"]
                if gap_info
                else None,

            "average_gap":
                gap_info["average_gap"]
                if gap_info
                else None

        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates[:20]


# ============================================================
# SIMPLE WALK-FORWARD BACKTEST
# ============================================================

def backtest(values):

    if len(values) < 100:

        return {
            "available": False,
            "reason":
                "At least 100 historical records are recommended.",
            "tests": 0,
            "hits": 0,
            "hit_rate": None
        }

    tests = 0
    hits = 0

    # Keep the test deliberately simple:
    # use previous history to rank frequent values,
    # then check whether the next observed value
    # appeared in the top-N historical candidates.
    start = 50

    for index in range(
        start,
        len(values)
    ):

        history = values[:index]

        counter = Counter(history)

        top_values = {
            value
            for value, _ in
            counter.most_common(10)
        }

        actual = values[index]

        tests += 1

        if actual in top_values:
            hits += 1

    hit_rate = (
        hits / tests * 100
        if tests
        else None
    )

    return {

        "available": True,

        "tests": tests,

        "hits": hits,

        "hit_rate":
            round(hit_rate, 2)
            if hit_rate is not None
            else None

    }


# ============================================================
# COMPLETE ANALYSIS
# ============================================================

def analyze(values):

    return {

        "records":
            len(values),

        "unique_values":
            len(set(values)),

        "frequency":
            frequency(values)[:50],

        "top_panels":
            frequency(values)[:20],

        "digit_frequency":
            digit_distribution(values),

        "position_distribution":
            position_distribution(values),

        "gap_statistics":
            gap_analysis(values),

        "recent_analysis":
            recent_analysis(values),

        "candidate_ranking":
            candidate_scores(values),

        "backtest":
            backtest(values)

    }


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def index():

    return render_template(
        "index.html"
    )


@app.get("/health")
def health():

    return jsonify({
        "ok": True,
        "service":
            "NEXO Analytics v2",
        "status":
            "online"
    })


@app.get("/api/markets")
def markets():

    return jsonify({

        "ok": True,

        "markets": {

            key: {
                "name":
                    market.name,

                "url":
                    market.url

            }

            for key, market
            in MARKETS.items()

        }

    })


@app.get("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan"
    ).strip().lower()

    market = MARKETS.get(
        market_key
    )

    if market is None:

        return jsonify({

            "ok": False,

            "error":
                "Unsupported market."

        }), 400

    try:

        html, status = fetch_source(
            market
        )

        values, rows = extract_values(
            html
        )

        if not values:

            return jsonify({

                "ok": False,

                "error":
                    "No 3-digit historical values were detected."

            }), 422

        result = analyze(
            values
        )

        return jsonify({

            "ok": True,

            "market": {

                "key":
                    market_key,

                "name":
                    market.name,

                "url":
                    market.url

            },

            "source": {

                "http_status":
                    status,

                "html_size":
                    len(html),

                "table_rows":
                    rows

            },

            "analysis":
                result

        })

    except requests.Timeout:

        return jsonify({

            "ok": False,

            "error":
                "Source server timed out."

        }), 504

    except requests.HTTPError as exc:

        return jsonify({

            "ok": False,

            "error":
                f"Source HTTP error: {exc}"

        }), 502

    except requests.RequestException as exc:

        return jsonify({

            "ok": False,

            "error":
                f"Source connection error: {exc}"

        }), 502

    except Exception as exc:

        app.logger.exception(
            "Analysis error"
        )

        return jsonify({

            "ok": False,

            "error":
                f"Internal analysis error: {exc}"

        }), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
