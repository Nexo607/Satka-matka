from flask import Flask, jsonify, render_template, request
import sqlite3
import re
import time
from datetime import datetime, timezone
from collections import Counter
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


app = Flask(__name__)

DATABASE = "nexo.db"

MARKETS = {
    "kalyan": {
        "name": "Kalyan",
        "url": "https://dpbossss.boston/panel-chart-record/kalyan.php",
    },
    "main-bazar": {
        "name": "Main Bazar",
        "url": "https://dpbossss.boston/panel-chart-record/main-bazar.php",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            record_date TEXT,
            panel TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(market, record_date, panel)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_records_market
        ON records(market)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_records_panel
        ON records(panel)
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# SOURCE VALIDATION
# ---------------------------------------------------------

def get_market(market):
    market = (market or "").strip().lower()

    if market not in MARKETS:
        raise ValueError(
            "Invalid market. Use 'kalyan' or 'main-bazar'."
        )

    return MARKETS[market]


def validate_source(url):
    """
    Only permit the two configured historical sources.
    This prevents the /api/sync endpoint becoming an open SSRF proxy.
    """
    allowed = {item["url"] for item in MARKETS.values()}

    if url not in allowed:
        raise ValueError("Source URL is not an approved historical source.")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError("Only HTTPS sources are allowed.")

    if parsed.netloc != "dpbossss.boston":
        raise ValueError("Unexpected source host.")

    return True


# ---------------------------------------------------------
# FETCH
# ---------------------------------------------------------

def fetch_source(url):
    validate_source(url)

    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=20,
                allow_redirects=True,
            )

            response.raise_for_status()

            if not response.text.strip():
                raise RuntimeError("The source returned an empty response.")

            return response.text

        except requests.RequestException as exc:
            last_error = str(exc)

            if attempt < 2:
                time.sleep(1.5)

    raise RuntimeError(
        f"Unable to fetch historical source after 3 attempts: {last_error}"
    )


# ---------------------------------------------------------
# PANEL EXTRACTION
# ---------------------------------------------------------

PANEL_PATTERN = re.compile(r"(?<!\d)(\d{3})(?!\d)")


def clean_panel(value):
    if value is None:
        return None

    value = str(value).strip()

    match = PANEL_PATTERN.search(value)

    if not match:
        return None

    return match.group(1)


def extract_date(text):
    """
    Attempts to find common date formats from table rows.
    """
    if not text:
        return None

    patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            return match.group(0)

    return None


def parse_html(html):
    """
    Extract historical 3-digit panels from HTML tables.

    The parser intentionally looks at table rows first because
    historical chart pages commonly contain date/panel columns.
    """

    soup = BeautifulSoup(html, "html.parser")

    records = []

    # -----------------------------------------------------
    # 1. TABLE-BASED EXTRACTION
    # -----------------------------------------------------

    for table in soup.find_all("table"):

        for row in table.find_all("tr"):

            cells = row.find_all(["td", "th"])

            if not cells:
                continue

            text_values = [
                cell.get_text(" ", strip=True)
                for cell in cells
            ]

            row_text = " ".join(text_values)

            # Ignore navigation/menu rows.
            if not row_text:
                continue

            panels = []

            for value in text_values:
                panel = clean_panel(value)

                if panel:
                    panels.append(panel)

            if not panels:
                continue

            record_date = extract_date(row_text)

            for panel in panels:
                records.append({
                    "date": record_date,
                    "panel": panel,
                })

    # -----------------------------------------------------
    # 2. FALLBACK EXTRACTION
    # -----------------------------------------------------

    if not records:

        page_text = soup.get_text(" ", strip=True)

        for match in PANEL_PATTERN.finditer(page_text):
            records.append({
                "date": None,
                "panel": match.group(1),
            })

    # -----------------------------------------------------
    # 3. DEDUPLICATE WITHIN FETCH
    # -----------------------------------------------------

    unique = []
    seen = set()

    for item in records:

        key = (
            item.get("date"),
            item["panel"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


# ---------------------------------------------------------
# DATABASE INSERT
# ---------------------------------------------------------

def store_records(market_key, source_url, records):
    conn = get_db()

    inserted = 0

    now = datetime.now(timezone.utc).isoformat()

    for item in records:

        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO records
                (
                    market,
                    record_date,
                    panel,
                    source,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    market_key,
                    item.get("date"),
                    item["panel"],
                    source_url,
                    now,
                ),
            )

            if cursor.rowcount == 1:
                inserted += 1

        except sqlite3.Error:
            continue

    conn.commit()
    conn.close()

    return inserted


# ---------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------

def calculate_analysis(market):
    conn = get_db()

    rows = conn.execute(
        """
        SELECT id, record_date, panel
        FROM records
        WHERE market = ?
        ORDER BY id ASC
        """,
        (market,),
    ).fetchall()

    conn.close()

    panels = [row["panel"] for row in rows]

    if not panels:
        return {
            "records": 0,
            "unique_panels": 0,
            "panel_frequency": [],
            "top_panels": [],
            "digit_frequency": [],
            "position_frequency": {
                "hundreds": [],
                "tens": [],
                "units": [],
            },
            "gaps": [],
        }

    # -----------------------------------------------------
    # PANEL FREQUENCY
    # -----------------------------------------------------

    panel_counts = Counter(panels)

    panel_frequency = [
        {
            "value": panel,
            "count": count,
        }
        for panel, count in panel_counts.most_common()
    ]

    # -----------------------------------------------------
    # DIGIT FREQUENCY
    # -----------------------------------------------------

    digit_counts = Counter()

    for panel in panels:
        for digit in panel:
            digit_counts[digit] += 1

    digit_frequency = [
        {
            "digit": digit,
            "count": digit_counts[digit],
        }
        for digit in "0123456789"
    ]

    # -----------------------------------------------------
    # POSITION FREQUENCY
    # -----------------------------------------------------

    hundreds = Counter()
    tens = Counter()
    units = Counter()

    for panel in panels:

        if len(panel) != 3:
            continue

        hundreds[panel[0]] += 1
        tens[panel[1]] += 1
        units[panel[2]] += 1

    position_frequency = {
        "hundreds": [
            {"digit": d, "count": hundreds[d]}
            for d in "0123456789"
        ],
        "tens": [
            {"digit": d, "count": tens[d]}
            for d in "0123456789"
        ],
        "units": [
            {"digit": d, "count": units[d]}
            for d in "0123456789"
        ],
    }

    # -----------------------------------------------------
    # GAP ANALYSIS
    # -----------------------------------------------------

    last_seen = {}
    gaps = []

    for index, panel in enumerate(panels):

        if panel in last_seen:

            gap = index - last_seen[panel]

            gaps.append({
                "panel": panel,
                "gap": gap,
                "position": index,
            })

        last_seen[panel] = index

    gaps.sort(
        key=lambda item: item["gap"],
        reverse=True,
    )

    # -----------------------------------------------------
    # HISTORICAL TOP PANELS
    # -----------------------------------------------------

    top_panels = panel_frequency[:20]

    return {
        "records": len(panels),
        "unique_panels": len(panel_counts),
        "panel_frequency": panel_frequency,
        "top_panels": top_panels,
        "digit_frequency": digit_frequency,
        "position_frequency": position_frequency,
        "gaps": gaps[:50],
    }


# ---------------------------------------------------------
# ROUTES
# ---------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        markets=MARKETS,
    )


@app.get("/api/markets")
def api_markets():

    return jsonify({
        "ok": True,
        "markets": [
            {
                "id": key,
                "name": value["name"],
                "url": value["url"],
            }
            for key, value in MARKETS.items()
        ],
    })


@app.get("/api/sync")
def api_sync():

    market_key = request.args.get("market", "").strip().lower()

    try:
        market = get_market(market_key)

        source_url = market["url"]

        html = fetch_source(source_url)

        records = parse_html(html)

        if not records:
            return jsonify({
                "ok": False,
                "error": (
                    "The source was reached, but no 3-digit historical "
                    "panel records could be detected."
                ),
                "market": market_key,
                "source": source_url,
            }), 422

        inserted = store_records(
            market_key,
            source_url,
            records,
        )

        analysis = calculate_analysis(market_key)

        return jsonify({
            "ok": True,
            "market": market_key,
            "market_name": market["name"],
            "source": source_url,
            "rows_found": len(records),
            "new_records": inserted,
            "analysis": analysis,
        })

    except Exception as exc:

        app.logger.exception("Historical sync failed")

        return jsonify({
            "ok": False,
            "error": str(exc),
            "market": market_key,
        }), 500


@app.get("/api/history")
def api_history():

    market_key = request.args.get("market", "").strip().lower()

    try:
        get_market(market_key)

        conn = get_db()

        rows = conn.execute(
            """
            SELECT
                record_date,
                panel,
                source,
                created_at
            FROM records
            WHERE market = ?
            ORDER BY id DESC
            """,
            (market_key,),
        ).fetchall()

        conn.close()

        return jsonify({
            "ok": True,
            "market": market_key,
            "records": [
                {
                    "date": row["record_date"],
                    "panel": row["panel"],
                    "source": row["source"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        })

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 400


@app.get("/api/analysis")
def api_analysis():

    market_key = request.args.get("market", "").strip().lower()

    try:
        get_market(market_key)

        analysis = calculate_analysis(market_key)

        return jsonify({
            "ok": True,
            "market": market_key,
            "analysis": analysis,
        })

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 400


@app.get("/health")
def health():

    return jsonify({
        "ok": True,
        "service": "NEXO Historical Analytics",
        "status": "LIVE",
    })


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
