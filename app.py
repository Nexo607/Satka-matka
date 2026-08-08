from flask import Flask, jsonify, request, render_template
import sqlite3
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

DB_FILE = "nexo.db"

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

ALLOWED_HOST = "dpbossss.boston"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS panels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            panel TEXT NOT NULL,
            source_url TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            UNIQUE(market, panel)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            rows_found INTEGER NOT NULL DEFAULT 0,
            panels_found INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error TEXT
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# SECURITY
# ---------------------------------------------------------

def validate_source(url):
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Invalid URL scheme")

    if parsed.hostname != ALLOWED_HOST:
        raise ValueError("Only the configured historical source is allowed")

    return True


# ---------------------------------------------------------
# FETCH
# ---------------------------------------------------------

def fetch_html(url):
    validate_source(url)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )

    response.raise_for_status()

    if not response.text:
        raise ValueError("Source returned empty HTML")

    return response.text


# ---------------------------------------------------------
# PANEL EXTRACTION
# ---------------------------------------------------------

def clean_cell_text(text):
    """
    Converts HTML table cell content such as:

        3
        4
        9

    into:

        349
    """

    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_three_digit_values(text):
    """
    Extract valid 3-digit panel values.

    We deliberately ignore:
        *
        **
        ***
        dates
        2-digit jodis
    """

    text = clean_cell_text(text)

    if "*" in text:
        return []

    found = []

    # Normal case: 349
    for match in re.findall(r"(?<!\d)\d{3}(?!\d)", text):
        found.append(match)

    return found


def parse_panel_table(html):
    """
    Parse panel-chart tables.

    The source renders historical panels inside table cells.
    We inspect table cells instead of scraping the entire page
    with one giant regex.
    """

    soup = BeautifulSoup(html, "html.parser")

    panels = []
    table_count = 0

    for table in soup.find_all("table"):
        table_count += 1

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])

            if not cells:
                continue

            for cell in cells:
                text = cell.get_text(" ", strip=True)

                values = extract_three_digit_values(text)

                for value in values:
                    # Preserve leading zeroes.
                    value = value.zfill(3)

                    # Basic validation.
                    if len(value) == 3 and value.isdigit():
                        panels.append(value)

    return panels, table_count


# ---------------------------------------------------------
# DATABASE STORAGE
# ---------------------------------------------------------

def save_panels(market_key, source_url, panels):

    now = datetime.now(timezone.utc).isoformat()

    conn = db()

    inserted = 0
    updated = 0

    for panel in panels:

        existing = conn.execute(
            """
            SELECT id, occurrences
            FROM panels
            WHERE market = ? AND panel = ?
            """,
            (market_key, panel),
        ).fetchone()

        if existing:

            conn.execute(
                """
                UPDATE panels
                SET occurrences = occurrences + 1,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, existing["id"]),
            )

            updated += 1

        else:

            conn.execute(
                """
                INSERT INTO panels
                (
                    market,
                    panel,
                    source_url,
                    first_seen,
                    last_seen,
                    occurrences
                )
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (
                    market_key,
                    panel,
                    source_url,
                    now,
                    now,
                ),
            )

            inserted += 1

    conn.commit()
    conn.close()

    return inserted, updated


# ---------------------------------------------------------
# STATISTICS
# ---------------------------------------------------------

def digit_frequency(market):
    conn = db()

    rows = conn.execute(
        """
        SELECT panel, occurrences
        FROM panels
        WHERE market = ?
        """,
        (market,),
    ).fetchall()

    conn.close()

    counts = {str(i): 0 for i in range(10)}

    for row in rows:
        panel = row["panel"]
        weight = row["occurrences"]

        for digit in panel:
            counts[digit] += weight

    return [
        {
            "digit": digit,
            "count": count
        }
        for digit, count in sorted(
            counts.items(),
            key=lambda x: x[1],
            reverse=True
        )
    ]


def top_panels(market, limit=50):
    conn = db()

    rows = conn.execute(
        """
        SELECT panel, occurrences, first_seen, last_seen
        FROM panels
        WHERE market = ?
        ORDER BY occurrences DESC, last_seen DESC
        LIMIT ?
        """,
        (market, limit),
    ).fetchall()

    conn.close()

    return [
        {
            "value": row["panel"],
            "count": row["occurrences"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }
        for row in rows
    ]


def market_stats(market):

    conn = db()

    total = conn.execute(
        """
        SELECT COALESCE(SUM(occurrences), 0)
        FROM panels
        WHERE market = ?
        """,
        (market,),
    ).fetchone()[0]

    unique = conn.execute(
        """
        SELECT COUNT(*)
        FROM panels
        WHERE market = ?
        """,
        (market,),
    ).fetchone()[0]

    last_sync = conn.execute(
        """
        SELECT fetched_at
        FROM sync_history
        WHERE market = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (market,),
    ).fetchone()

    conn.close()

    return {
        "records": total,
        "unique": unique,
        "last_sync": last_sync["fetched_at"] if last_sync else None,
        "digit_frequency": digit_frequency(market),
        "top_panels": top_panels(market),
    }


# ---------------------------------------------------------
# SYNC
# ---------------------------------------------------------

def sync_market(market_key):

    if market_key not in MARKETS:
        raise ValueError("Unknown market")

    market = MARKETS[market_key]
    source_url = market["url"]

    started = time.time()

    html = fetch_html(source_url)

    panels, table_count = parse_panel_table(html)

    if not panels:
        raise ValueError(
            "No 3-digit panel values were extracted. "
            "The source HTML structure may have changed."
        )

    inserted, updated = save_panels(
        market_key,
        source_url,
        panels,
    )

    elapsed = round(time.time() - started, 2)

    now = datetime.now(timezone.utc).isoformat()

    conn = db()

    conn.execute(
        """
        INSERT INTO sync_history
        (
            market,
            fetched_at,
            rows_found,
            panels_found,
            status,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            market_key,
            now,
            table_count,
            len(panels),
            "success",
            None,
        ),
    )

    conn.commit()
    conn.close()

    stats = market_stats(market_key)

    return {
        "ok": True,
        "market": market["name"],
        "source": source_url,
        "tables_found": table_count,
        "panels_extracted": len(panels),
        "new_unique_panels": inserted,
        "existing_panels_updated": updated,
        "elapsed_seconds": elapsed,
        "analysis": stats,
    }


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/markets")
def markets():
    return jsonify({
        "ok": True,
        "markets": [
            {
                "id": key,
                "name": value["name"],
                "url": value["url"],
            }
            for key, value in MARKETS.items()
        ]
    })


@app.route("/api/sync")
def api_sync():

    market = request.args.get("market", "kalyan").strip().lower()

    try:

        result = sync_market(market)

        return jsonify(result)

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc),
            "market": market,
        }), 500


@app.route("/api/stats")
def api_stats():

    market = request.args.get("market", "kalyan").strip().lower()

    if market not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 400

    return jsonify({
        "ok": True,
        "market": MARKETS[market]["name"],
        "analysis": market_stats(market)
    })


@app.route("/api/top")
def api_top():

    market = request.args.get("market", "kalyan").strip().lower()

    if market not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Unknown market"
        }), 400

    limit = request.args.get("limit", "50")

    try:
        limit = min(max(int(limit), 1), 500)
    except ValueError:
        limit = 50

    return jsonify({
        "ok": True,
        "market": MARKETS[market]["name"],
        "items": top_panels(market, limit)
    })


@app.route("/api/health")
def health():

    conn = db()

    count = conn.execute(
        "SELECT COUNT(*) FROM panels"
    ).fetchone()[0]

    conn.close()

    return jsonify({
        "ok": True,
        "service": "NEXO Historical Analytics",
        "database_records": count,
        "status": "LIVE"
    })


# ---------------------------------------------------------
# START
# ---------------------------------------------------------

init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
