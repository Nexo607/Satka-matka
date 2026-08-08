from flask import Flask, jsonify, render_template, request
from bs4 import BeautifulSoup
from collections import Counter
from datetime import datetime, timezone
import sqlite3
import re
import requests
import os
import math

app = Flask(__name__)

DATABASE = "nexo.db"

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
        "AppleWebKit/605.1.15 Safari/604.1"
    )
}


def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            panel TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(market, panel)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            records INTEGER NOT NULL,
            synced_at TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def fetch_page(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    if not response.text:
        raise RuntimeError("Empty response from source")

    return response.text


def extract_panels(html):
    """
    Extract only 3-digit panel values from HTML table cells.

    A panel such as:

        7
        8
        0

    becomes:

        780

    We deliberately ignore dates, years and 2-digit Jodi values.
    """

    soup = BeautifulSoup(html, "html.parser")

    panels = []

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

        for row in rows:

            cells = row.find_all(["td", "th"])

            for cell in cells:

                text = cell.get_text(" ", strip=True)

                # Remove whitespace between stacked digits.
                compact = re.sub(r"\s+", "", text)

                # Only accept exactly 3 numerical digits.
                if re.fullmatch(r"\d{3}", compact):
                    panels.append(compact)

    # Fallback parser for pages where table markup changes.
    if not panels:

        text = soup.get_text(" ", strip=True)

        candidates = re.findall(r"\b\d{3}\b", text)

        # Filter obvious years.
        for value in candidates:
            if not value.startswith(("19", "20")):
                panels.append(value)

    return panels


def save_panels(market, panels, source):
    conn = db()

    now = datetime.now(timezone.utc).isoformat()

    inserted = 0

    for panel in panels:

        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO panels
                (market, panel, source, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                market,
                panel,
                source,
                now
            ))

            if cur.rowcount:
                inserted += 1

        except sqlite3.Error:
            pass

    conn.commit()
    conn.close()

    return inserted


def calculate_analysis(market):
    conn = db()

    rows = conn.execute("""
        SELECT panel, created_at
        FROM panels
        WHERE market = ?
        ORDER BY id ASC
    """, (market,)).fetchall()

    conn.close()

    values = [row["panel"] for row in rows]

    if not values:
        return {
            "records": 0,
            "unique": 0,
            "frequency": [],
            "digit_frequency": [],
            "gap_analysis": [],
            "ranking": []
        }

    counter = Counter(values)

    frequency = [
        {
            "value": value,
            "count": count
        }
        for value, count in counter.most_common(50)
    ]

    digit_counter = Counter()

    for value in values:
        for digit in value:
            digit_counter[digit] += 1

    digit_frequency = [
        {
            "digit": digit,
            "count": digit_counter[digit]
        }
        for digit in "0123456789"
    ]

    # Gap analysis:
    # number of observations since each panel last appeared.
    last_seen = {}

    for index, value in enumerate(values):
        last_seen[value] = index

    total = len(values)

    gap_data = []

    for value, count in counter.items():

        last_index = last_seen[value]

        gap = total - 1 - last_index

        gap_data.append({
            "value": value,
            "count": count,
            "gap": gap
        })

    gap_data.sort(
        key=lambda x: (x["gap"], -x["count"])
    )

    # Historical statistical ranking.
    #
    # This is descriptive analysis only.
    # It is NOT a prediction of a future gambling result.

    ranking = []

    for value, count in counter.items():

        gap = total - 1 - last_seen[value]

        frequency_score = count / total

        recent_window = values[-100:]

        recent_count = recent_window.count(value)

        recent_score = (
            recent_count / len(recent_window)
            if recent_window
            else 0
        )

        gap_score = 1 / (1 + gap)

        score = (
            frequency_score * 0.45 +
            recent_score * 0.35 +
            gap_score * 0.20
        )

        ranking.append({
            "value": value,
            "count": count,
            "gap": gap,
            "score": round(score * 100, 4)
        })

    ranking.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return {
        "records": total,
        "unique": len(counter),
        "frequency": frequency,
        "digit_frequency": digit_frequency,
        "gap_analysis": gap_data[:50],
        "ranking": ranking[:30]
    }


def sync_market(market):
    if market not in MARKETS:
        raise ValueError("Unknown market")

    config = MARKETS[market]

    html = fetch_page(config["url"])

    panels = extract_panels(html)

    if not panels:
        raise RuntimeError(
            "No 3-digit panel values were detected. "
            "The source page structure may have changed."
        )

    inserted = save_panels(
        market,
        panels,
        config["url"]
    )

    conn = db()

    conn.execute("""
        INSERT INTO sync_log
        (market, records, synced_at, status)
        VALUES (?, ?, ?, ?)
    """, (
        market,
        inserted,
        datetime.now(timezone.utc).isoformat(),
        "OK"
    ))

    conn.commit()
    conn.close()

    return {
        "market": market,
        "name": config["name"],
        "source": config["url"],
        "fetched": len(panels),
        "new_records": inserted,
        "analysis": calculate_analysis(market)
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        markets=MARKETS
    )


@app.route("/api/markets")
def markets():
    return jsonify({
        "ok": True,
        "markets": MARKETS
    })


@app.route("/api/sync/<market>")
def api_sync(market):

    try:
        result = sync_market(market)

        return jsonify({
            "ok": True,
            **result
        })

    except requests.RequestException as e:

        return jsonify({
            "ok": False,
            "error": f"Source request failed: {str(e)}"
        }), 502

    except Exception as e:

        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/analysis/<market>")
def api_analysis(market):

    if market not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    analysis = calculate_analysis(market)

    return jsonify({
        "ok": True,
        "market": market,
        "name": MARKETS[market]["name"],
        "analysis": analysis
    })


@app.route("/api/status/<market>")
def api_status(market):

    if market not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    conn = db()

    total = conn.execute("""
        SELECT COUNT(*)
        FROM panels
        WHERE market = ?
    """, (market,)).fetchone()[0]

    last_sync = conn.execute("""
        SELECT synced_at
        FROM sync_log
        WHERE market = ?
        ORDER BY id DESC
        LIMIT 1
    """, (market,)).fetchone()

    conn.close()

    return jsonify({
        "ok": True,
        "market": market,
        "records": total,
        "last_sync": (
            last_sync["synced_at"]
            if last_sync
            else None
        )
    })


@app.route("/api/clear/<market>", methods=["POST"])
def clear_market(market):

    if market not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 404

    conn = db()

    conn.execute(
        "DELETE FROM panels WHERE market = ?",
        (market,)
    )

    conn.execute(
        "DELETE FROM sync_log WHERE market = ?",
        (market,)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "message": f"{MARKETS[market]['name']} database cleared"
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "service": "NEXO Historical Analytics"
    })


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
