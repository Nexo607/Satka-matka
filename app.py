from flask import Flask, jsonify, render_template
from bs4 import BeautifulSoup
from collections import Counter
from datetime import datetime, timezone
import sqlite3
import requests
import re
import os
import statistics

app = Flask(__name__)

DB = "nexo.db"

MARKETS = {
    "kalyan": {
        "name": "Kalyan",
        "url": "https://dpbossss.boston/panel-chart-record/kalyan.php",
    },
    "main_bazar": {
        "name": "Main Bazar",
        "url": "https://dpbossss.boston/panel-chart-record/main-bazar.php",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            panel TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(market, panel)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            fetched INTEGER NOT NULL,
            inserted INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# SOURCE FETCH
# ---------------------------------------------------------

def fetch_source(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    if not response.text:
        raise RuntimeError("Source returned empty HTML.")

    return response.text


# ---------------------------------------------------------
# PANEL PARSER
# ---------------------------------------------------------

def parse_panels(html):
    """
    Parse 3-digit panel values from the historical HTML tables.

    We inspect table cells first instead of blindly running
    one regex across the entire document.
    """

    soup = BeautifulSoup(html, "html.parser")

    panels = []

    for table in soup.find_all("table"):

        for row in table.find_all("tr"):

            cells = row.find_all(["td", "th"])

            for cell in cells:

                # Preserve the raw cell content.
                raw = cell.get_text(" ", strip=True)

                # Remove whitespace/newlines between digits.
                compact = re.sub(r"\s+", "", raw)

                # Standard 3-digit panel.
                if re.fullmatch(r"\d{3}", compact):
                    panels.append(compact)

    # Fallback if the page has unusual table markup.
    if not panels:

        text = soup.get_text(" ", strip=True)

        for value in re.findall(r"(?<!\d)\d{3}(?!\d)", text):

            # Ignore obvious years.
            if value.startswith(("19", "20")):
                continue

            panels.append(value)

    return panels


# ---------------------------------------------------------
# SAVE
# ---------------------------------------------------------

def save_panels(market, source, panels):

    conn = get_db()

    timestamp = datetime.now(timezone.utc).isoformat()

    inserted = 0

    for panel in panels:

        cursor = conn.execute("""
            INSERT OR IGNORE INTO panels
            (market, panel, source, fetched_at)
            VALUES (?, ?, ?, ?)
        """, (
            market,
            panel,
            source,
            timestamp
        ))

        if cursor.rowcount:
            inserted += 1

    conn.commit()
    conn.close()

    return inserted


# ---------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------

def load_panels(market):

    conn = get_db()

    rows = conn.execute("""
        SELECT panel
        FROM panels
        WHERE market = ?
        ORDER BY id ASC
    """, (market,)).fetchall()

    conn.close()

    return [row["panel"] for row in rows]


# ---------------------------------------------------------
# NEXO ALGORITHM
# ---------------------------------------------------------

def calculate_nexo(values):

    if not values:
        return {
            "records": 0,
            "unique": 0,
            "ranking": [],
            "digits": [],
            "gaps": [],
            "frequency": []
        }

    total = len(values)

    overall = Counter(values)

    # Recent windows
    windows = {
        25: values[-25:],
        50: values[-50:],
        100: values[-100:]
    }

    counters = {
        size: Counter(data)
        for size, data in windows.items()
    }

    # -----------------------------------------------------
    # LAST APPEARANCE
    # -----------------------------------------------------

    last_seen = {}

    for index, panel in enumerate(values):
        last_seen[panel] = index

    # -----------------------------------------------------
    # POSITION DISTRIBUTION
    # -----------------------------------------------------

    position = [
        Counter(),
        Counter(),
        Counter()
    ]

    for panel in values:

        if len(panel) != 3:
            continue

        position[0][panel[0]] += 1
        position[1][panel[1]] += 1
        position[2][panel[2]] += 1

    # -----------------------------------------------------
    # PERIOD CONSISTENCY
    # -----------------------------------------------------

    periods = []

    period_size = 30

    for i in range(0, total, period_size):
        periods.append(
            set(values[i:i + period_size])
        )

    # -----------------------------------------------------
    # MAX VALUES
    # -----------------------------------------------------

    max_frequency = max(overall.values())

    recent100 = counters[100]

    max_recent = (
        max(recent100.values())
        if recent100
        else 1
    )

    ranking = []

    # -----------------------------------------------------
    # SCORE EACH PANEL
    # -----------------------------------------------------

    for panel, count in overall.items():

        # 1. Historical frequency
        frequency = count / max_frequency

        # 2. Recent frequency
        recent25 = (
            counters[25][panel] /
            max(counters[25].values(), default=1)
        )

        recent50 = (
            counters[50][panel] /
            max(counters[50].values(), default=1)
        )

        recent100_score = (
            counters[100][panel] /
            max_recent
        )

        recent = (
            recent25 * 0.50 +
            recent50 * 0.30 +
            recent100_score * 0.20
        )

        # 3. Gap
        gap = total - 1 - last_seen[panel]

        gap_signal = 1 / (1 + gap)

        # 4. Digit-position strength
        digit_strength = 0

        if len(panel) == 3:

            hundreds = (
                position[0][panel[0]] / total
            )

            tens = (
                position[1][panel[1]] / total
            )

            units = (
                position[2][panel[2]] / total
            )

            digit_strength = (
                hundreds +
                tens +
                units
            ) / 3

        # 5. Repeat strength
        repeat_strength = min(
            count / max(total * 0.01, 1),
            1
        )

        # 6. Period consistency
        appearances = sum(
            1
            for period in periods
            if panel in period
        )

        consistency = (
            appearances / len(periods)
            if periods
            else 0
        )

        # -------------------------------------------------
        # PROVIDED METHODOLOGY → COMPOSITE SCORE
        # -------------------------------------------------

        score = (

            frequency * 0.25 +

            recent * 0.25 +

            repeat_strength * 0.15 +

            digit_strength * 0.15 +

            gap_signal * 0.10 +

            consistency * 0.10

        )

        ranking.append({
            "panel": panel,
            "frequency": count,
            "gap": gap,
            "frequency_score":
                round(frequency * 100, 2),
            "recent_score":
                round(recent * 100, 2),
            "repeat_score":
                round(repeat_strength * 100, 2),
            "digit_score":
                round(digit_strength * 100, 2),
            "consistency_score":
                round(consistency * 100, 2),
            "score":
                round(score * 100, 2)
        })

    ranking.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # -----------------------------------------------------
    # DIGIT FREQUENCY
    # -----------------------------------------------------

    digit_counter = Counter()

    for panel in values:
        for digit in panel:
            digit_counter[digit] += 1

    digits = [
        {
            "digit": str(d),
            "count": digit_counter[str(d)]
        }
        for d in range(10)
    ]

    # -----------------------------------------------------
    # FREQUENCY
    # -----------------------------------------------------

    frequency = [
        {
            "panel": panel,
            "count": count
        }
        for panel, count
        in overall.most_common(30)
    ]

    # -----------------------------------------------------
    # GAP TABLE
    # -----------------------------------------------------

    gaps = []

    for panel, count in overall.items():

        gap = total - 1 - last_seen[panel]

        gaps.append({
            "panel": panel,
            "count": count,
            "gap": gap
        })

    gaps.sort(
        key=lambda x: x["gap"],
        reverse=True
    )

    return {
        "records": total,
        "unique": len(overall),
        "ranking": ranking[:30],
        "digits": digits,
        "frequency": frequency,
        "gaps": gaps[:30]
    }


# ---------------------------------------------------------
# BACKTEST
# ---------------------------------------------------------

def backtest(values):

    if len(values) < 100:
        return {
            "available": False,
            "message": "At least 100 historical records required."
        }

    split = int(len(values) * 0.70)

    training = values[:split]
    testing = values[split:]

    analysis = calculate_nexo(training)

    ranked = {
        item["panel"]
        for item in analysis["ranking"][:10]
    }

    hits = sum(
        1
        for value in testing
        if value in ranked
    )

    hit_rate = (
        hits / len(testing) * 100
        if testing
        else 0
    )

    return {
        "available": True,
        "training_records": len(training),
        "testing_records": len(testing),
        "top_candidates": list(ranked),
        "historical_hit_rate":
            round(hit_rate, 2)
    }


# ---------------------------------------------------------
# SYNC
# ---------------------------------------------------------

def sync_market(market):

    if market not in MARKETS:
        raise ValueError("Unknown market.")

    config = MARKETS[market]

    html = fetch_source(config["url"])

    panels = parse_panels(html)

    if not panels:
        raise RuntimeError(
            "No panel values were extracted. "
            "The source HTML structure may have changed."
        )

    inserted = save_panels(
        market,
        config["url"],
        panels
    )

    values = load_panels(market)

    analysis = calculate_nexo(values)

    test = backtest(values)

    conn = get_db()

    conn.execute("""
        INSERT INTO sync_log
        (market, fetched, inserted, timestamp, status, error)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        market,
        len(panels),
        inserted,
        datetime.now(timezone.utc).isoformat(),
        "SUCCESS",
        None
    ))

    conn.commit()
    conn.close()

    return {
        "market": market,
        "name": config["name"],
        "source": config["url"],
        "fetched": len(panels),
        "inserted": inserted,
        "analysis": analysis,
        "backtest": test
    }


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/sync/<market>")
def api_sync(market):

    try:

        return jsonify({
            "ok": True,
            **sync_market(market)
        })

    except Exception as error:

        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


@app.route("/api/analysis/<market>")
def api_analysis(market):

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    values = load_panels(market)

    return jsonify({
        "ok": True,
        "market": market,
        "name": MARKETS[market]["name"],
        "analysis": calculate_nexo(values),
        "backtest": backtest(values)
    })


@app.route("/api/status/<market>")
def api_status(market):

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    values = load_panels(market)

    conn = get_db()

    row = conn.execute("""
        SELECT timestamp
        FROM sync_log
        WHERE market = ?
        ORDER BY id DESC
        LIMIT 1
    """, (market,)).fetchone()

    conn.close()

    return jsonify({
        "ok": True,
        "market": market,
        "records": len(values),
        "last_sync":
            row["timestamp"]
            if row
            else None
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "service": "NEXO Historical Analytics v4"
    })


init_db()


if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
