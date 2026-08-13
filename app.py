from __future__ import annotations

import math
import re
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request


# ============================================================
# NEXO // HISTORICAL ANALYTICS v6
# ============================================================

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
    "Accept": "text/html,application/xhtml+xml,"
              "application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS panel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            panel TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_panel_history_market
        ON panel_history(market)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_panel_history_panel
        ON panel_history(panel)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            source TEXT NOT NULL,
            rows_found INTEGER NOT NULL,
            panels_found INTEGER NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# ERROR HANDLING
# ============================================================

@app.errorhandler(Exception)
def handle_exception(exc):
    app.logger.exception("UNHANDLED ERROR")

    return jsonify({
        "ok": False,
        "error": f"{type(exc).__name__}: {str(exc)}"
    }), 500


# ============================================================
# URL VALIDATION
# ============================================================

def valid_source(url: str) -> bool:
    return url in {
        item["url"]
        for item in MARKETS.values()
    }


def validate_source(url: str):
    if not isinstance(url, str):
        raise ValueError("Source URL is not a string.")

    url = url.strip()

    if not url:
        raise ValueError("Source URL is empty.")

    if not valid_source(url):
        raise ValueError("Unknown historical source.")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError("Historical source must use HTTPS.")

    if not parsed.netloc:
        raise ValueError("Historical source URL is invalid.")

    return url


# ============================================================
# SOURCE FETCHING
# ============================================================

def fetch_source(url: str) -> str:

    url = validate_source(url)

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=(10, 30),
        allow_redirects=True,
    )

    response.raise_for_status()

    if not response.text.strip():
        raise ValueError(
            "Historical source returned an empty response."
        )

    return response.text


# ============================================================
# PANEL PARSING
# ============================================================

def normalize_panel(value):

    if value is None:
        return None

    digits = re.findall(
        r"\d",
        str(value)
    )

    if len(digits) != 3:
        return None

    return "".join(digits)


def extract_panels_from_cell(cell):

    text = cell.get_text(
        " ",
        strip=True
    )

    if not text:
        return []

    if "*" in text:
        return []

    found = []

    # Example:
    # 7 8 0
    # 7 8 0 1 2 3
    matches = re.findall(
        r"(?<!\d)(\d)\s+(\d)\s+(\d)(?!\d)",
        text
    )

    for a, b, c in matches:

        panel = f"{a}{b}{c}"

        if re.fullmatch(
            r"\d{3}",
            panel
        ):
            found.append(panel)

    if not found:

        compact = re.sub(
            r"\s+",
            "",
            text
        )

        if re.fullmatch(
            r"\d{3}",
            compact
        ):
            found.append(compact)

    return found


def parse_panels(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    panels = []
    rows_found = 0

    # --------------------------------------------------------
    # TABLE PARSER
    # --------------------------------------------------------

    for table in soup.find_all("table"):

        for tr in table.find_all("tr"):

            cells = tr.find_all(
                ["td", "th"]
            )

            if not cells:
                continue

            row_text = tr.get_text(
                " ",
                strip=True
            )

            lower = row_text.lower()

            if (
                "date" in lower
                and "mon" in lower
            ):
                continue

            row_panels = []

            for cell in cells:

                row_panels.extend(
                    extract_panels_from_cell(
                        cell
                    )
                )

            if row_panels:

                rows_found += 1

                panels.extend(
                    row_panels
                )

    # --------------------------------------------------------
    # FALLBACK TEXT PARSER
    # --------------------------------------------------------

    if not panels:

        text = soup.get_text(
            "\n",
            strip=True
        )

        for line in text.splitlines():

            line = line.strip()

            if not line:
                continue

            compact = re.sub(
                r"\s+",
                "",
                line
            )

            if re.fullmatch(
                r"\d{3}",
                compact
            ):

                panels.append(
                    compact
                )

    # --------------------------------------------------------
    # CLEAN
    # --------------------------------------------------------

    cleaned = []

    for panel in panels:

        normalized = normalize_panel(
            panel
        )

        if normalized:
            cleaned.append(
                normalized
            )

    return cleaned, rows_found


# ============================================================
# DATABASE STORAGE
# ============================================================

def save_panels(
    market,
    panels,
    source
):

    now = datetime.now(
        timezone.utc
    ).isoformat()

    conn = db()

    inserted = 0

    for panel in panels:

        conn.execute(
            """
            INSERT INTO panel_history
            (market, panel, source, fetched_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                market,
                panel,
                source,
                now,
            )
        )

        inserted += 1

    conn.execute(
        """
        INSERT INTO sync_log
        (market, source, rows_found,
         panels_found, synced_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            market,
            source,
            len(panels),
            inserted,
            now,
        )
    )

    conn.commit()
    conn.close()

    return inserted


def get_database_panels(market):

    conn = db()

    rows = conn.execute(
        """
        SELECT panel
        FROM panel_history
        WHERE market = ?
        ORDER BY id ASC
        """,
        (market,)
    ).fetchall()

    conn.close()

    return [
        row["panel"]
        for row in rows
    ]


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_values(values):

    if not values:
        return []

    lo = min(values)
    hi = max(values)

    if hi == lo:
        return [
            0.5
            for _ in values
        ]

    return [
        (value - lo) /
        (hi - lo)
        for value in values
    ]


# ============================================================
# STATISTICAL ENGINE
# ============================================================

def analyze_panels(panels):

    if not panels:

        return {
            "records": 0,
            "unique": 0,
            "ranking": [],
            "top_panels": [],
            "digit_frequency": [],
            "backtest": {
                "available": False,
                "message":
                    "No historical observations available."
            }
        }

    counter = Counter(
        panels
    )

    total = len(
        panels
    )

    recent_window = min(
        100,
        total
    )

    recent = panels[
        -recent_window:
    ]

    recent_counter = Counter(
        recent
    )

    last_seen = {}

    for index, panel in enumerate(
        panels
    ):
        last_seen[
            panel
        ] = index

    raw = []

    for panel in counter:

        frequency = counter[
            panel
        ]

        recent_frequency = (
            recent_counter[
                panel
            ]
        )

        gap = (
            total
            - 1
            - last_seen[
                panel
            ]
        )

        recency = math.exp(
            -gap /
            max(
                25.0,
                total * 0.08
            )
        )

        momentum = (
            recent_frequency /
            max(
                1,
                frequency
            )
        )

        digits = [
            int(d)
            for d in panel
        ]

        digit_variance = (
            statistics.pvariance(
                digits
            )
            if len(digits) > 1
            else 0.0
        )

        digit_stability = (
            1.0 /
            (
                1.0 +
                digit_variance
            )
        )

        raw.append({
            "panel": panel,
            "frequency": frequency,
            "recent_frequency":
                recent_frequency,
            "gap": gap,
            "recency": recency,
            "momentum": momentum,
            "digit_stability":
                digit_stability,
        })

    feature_names = [
        "frequency",
        "recent_frequency",
        "recency",
        "momentum",
        "digit_stability",
    ]

    normalized = {}

    for feature in feature_names:

        values = [
            float(item[feature])
            for item in raw
        ]

        norm = normalize_values(
            values
        )

        for index, value in enumerate(
            norm
        ):
            normalized[
                (index, feature)
            ] = value

    weights = {
        "frequency": 0.30,
        "recent_frequency": 0.20,
        "recency": 0.15,
        "momentum": 0.15,
        "digit_stability": 0.20,
    }

    ranking = []

    for index, item in enumerate(
        raw
    ):

        score = 0.0

        for feature, weight in (
            weights.items()
        ):

            score += (
                normalized[
                    (index, feature)
                ]
                * weight
            )

        ranking.append({
            **item,
            "score": round(
                score * 100,
                2
            )
        })

    ranking.sort(
        key=lambda item: (
            -item["score"],
            -item["frequency"],
            item["gap"],
            item["panel"],
        )
    )

    # --------------------------------------------------------
    # DIGIT FREQUENCY
    # --------------------------------------------------------

    digit_counter = Counter()

    for panel in panels:

        for digit in panel:
            digit_counter[
                digit
            ] += 1

    digit_frequency = [
        {
            "digit": digit,
            "count":
                digit_counter[digit]
        }
        for digit in sorted(
            digit_counter
        )
    ]

    return {
        "records": total,
        "unique": len(counter),

        "ranking":
            ranking[:10],

        "top_panels": [
            {
                "value":
                    item["panel"],
                "count":
                    item["frequency"],
            }
            for item in ranking[:20]
        ],

        "digit_frequency":
            digit_frequency,

        "backtest":
            simple_backtest(
                panels
            ),
    }


# ============================================================
# BACKTEST
# ============================================================

def analyze_panels_without_backtest(
    panels
):

    if not panels:
        return {
            "ranking": []
        }

    counter = Counter(
        panels
    )

    total = len(
        panels
    )

    recent = panels[
        -min(100, total):
    ]

    recent_counter = Counter(
        recent
    )

    last_seen = {}

    for index, panel in enumerate(
        panels
    ):
        last_seen[
            panel
        ] = index

    raw = []

    for panel in counter:

        frequency = counter[
            panel
        ]

        recent_frequency = (
            recent_counter[
                panel
            ]
        )

        gap = (
            total
            - 1
            - last_seen[
                panel
            ]
        )

        recency = math.exp(
            -gap /
            max(
                25.0,
                total * 0.08
            )
        )

        momentum = (
            recent_frequency /
            max(
                1,
                frequency
            )
        )

        digits = [
            int(d)
            for d in panel
        ]

        variance = (
            statistics.pvariance(
                digits
            )
            if len(digits) > 1
            else 0.0
        )

        stability = (
            1.0 /
            (
                1.0 +
                variance
            )
        )

        raw.append({
            "panel": panel,
            "frequency": frequency,
            "recent_frequency":
                recent_frequency,
            "gap": gap,
            "recency": recency,
            "momentum": momentum,
            "digit_stability":
                stability,
        })

    features = [
        "frequency",
        "recent_frequency",
        "recency",
        "momentum",
        "digit_stability",
    ]

    normalized = {}

    for feature in features:

        values = [
            float(item[feature])
            for item in raw
        ]

        norm = normalize_values(
            values
        )

        for index, value in enumerate(
            norm
        ):

            normalized[
                (index, feature)
            ] = value

    weights = {
        "frequency": 0.30,
        "recent_frequency": 0.20,
        "recency": 0.15,
        "momentum": 0.15,
        "digit_stability": 0.20,
    }

    ranking = []

    for index, item in enumerate(
        raw
    ):

        score = sum(
            normalized[
                (index, feature)
            ]
            * weights[feature]
            for feature in features
        )

        ranking.append({
            **item,
            "score":
                score * 100
        })

    ranking.sort(
        key=lambda item: (
            -item["score"],
            -item["frequency"],
            item["gap"],
            item["panel"],
        )
    )

    return {
        "ranking": ranking
    }


def simple_backtest(panels):

    if len(panels) < 30:

        return {
            "available": False,
            "message":
                "Need at least 30 historical observations."
        }

    hits = 0
    trials = 0

    start = max(
        20,
        len(panels) - 250
    )

    for index in range(
        start,
        len(panels)
    ):

        train = panels[
            :index
        ]

        if len(set(train)) < 10:
            continue

        result = (
            analyze_panels_without_backtest(
                train
            )
        )

        top10 = {
            item["panel"]
            for item in result[
                "ranking"
            ][:10]
        }

        actual = panels[
            index
        ]

        if actual in top10:
            hits += 1

        trials += 1

    if trials == 0:

        return {
            "available": False,
            "message":
                "No valid walk-forward trials."
        }

    rate = (
        hits /
        trials
    )

    return {
        "available": True,
        "trials": trials,
        "top10_hits": hits,
        "hit_rate": round(
            rate * 100,
            2
        ),
        "warning":
            "Historical backtest only; "
            "not a guarantee of future outcomes."
    }


# ============================================================
# API
# ============================================================

@app.get("/api/health")
def health():

    return jsonify({
        "ok": True,
        "service":
            "NEXO Historical Analytics v6"
    })


@app.get("/api/markets")
def markets():

    return jsonify([
        {
            "id": market_id,
            "name": data["name"]
        }
        for market_id, data
        in MARKETS.items()
    ])


@app.get("/api/database")
def database_stats():

    conn = db()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM panel_history
        """
    ).fetchone()[0]

    unique = conn.execute(
        """
        SELECT COUNT(DISTINCT panel)
        FROM panel_history
        """
    ).fetchone()[0]

    conn.close()

    return jsonify({
        "ok": True,
        "records": total,
        "unique": unique
    })


@app.post("/api/sync")
def sync():

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    market = str(
        payload.get(
            "market",
            ""
        )
    ).strip().lower()

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error":
                "Select Kalyan or Main Bazar."
        }), 400

    source = MARKETS[
        market
    ]["url"]

    try:

        # ----------------------------------------------------
        # FETCH
        # ----------------------------------------------------

        html = fetch_source(
            source
        )

        # ----------------------------------------------------
        # PARSE
        # ----------------------------------------------------

        panels, rows_found = (
            parse_panels(
                html
            )
        )

        if not panels:

            return jsonify({
                "ok": False,
                "error":
                    "Source was reached, but "
                    "no 3-digit panel records "
                    "were detected."
            }), 502

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        new_records = save_panels(
            market,
            panels,
            source
        )

        # ----------------------------------------------------
        # ANALYZE ACCUMULATED MARKET HISTORY
        # ----------------------------------------------------

        all_panels = (
            get_database_panels(
                market
            )
        )

        analysis = analyze_panels(
            all_panels
        )

        response = {
            "ok": True,
            "market":
                MARKETS[
                    market
                ]["name"],
            "source": source,
            "rows_found":
                rows_found,
            "fetched_panels":
                len(panels),
            "new_records":
                new_records,
            "database_records":
                len(all_panels),
            "analysis":
                analysis
        }

        return jsonify(response), 200

    except requests.Timeout:

        return jsonify({
            "ok": False,
            "error":
                "Source request timed out. "
                "Try again."
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
                f"Source request failed: {exc}"
        }), 502

    except Exception as exc:

        app.logger.exception(
            "SYNC ERROR"
        )

        return jsonify({
            "ok": False,
            "error":
                f"{type(exc).__name__}: {str(exc)}"
        }), 500


# ============================================================
# MOBILE DASHBOARD
# ============================================================

HTML = r"""
<!doctype html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>NEXO Historical Analytics v6</title>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>

:root{
    --bg:#05080d;
    --panel:#08121c;
    --cyan:#00e5ff;
    --green:#00ff88;
    --red:#ff5577;
    --yellow:#ffcf66;
    --muted:#8195ac;
    --line:#173043;
}

*{
    box-sizing:border-box;
}

body{
    margin:0;
    background:
    radial-gradient(
        circle at top,
        #0b1d2c,
        #05080d 60%
    );
    color:#dffcff;
    font-family:
    ui-monospace,
    SFMono-Regular,
    Menlo,
    Monaco,
    Consolas,
    monospace;
}

header{
    padding:24px 20px;
    border-bottom:1px solid var(--line);
}

.brand{
    color:var(--cyan);
    font-size:24px;
    font-weight:900;
    letter-spacing:.08em;
}

.status{
    margin-top:12px;
    color:var(--green);
    line-height:1.6;
}

main{
    max-width:1100px;
    margin:auto;
    padding:18px;
}

.card{
    background:#08121cee;
    border:1px solid var(--line);
    border-radius:16px;
    padding:18px;
    margin-bottom:16px;
    box-shadow:
    0 0 30px #00e5ff08;
}

h2{
    color:var(--cyan);
    font-size:15px;
    letter-spacing:.1em;
}

select,
button{
    width:100%;
    padding:15px;
    margin-top:10px;
    border-radius:10px;
    border:1px solid #00e5ff77;
    background:#06141e;
    color:#dffcff;
    font:inherit;
}

button{
    background:#07303a;
    color:var(--cyan);
    cursor:pointer;
}

button:disabled{
    opacity:.5;
}

.metric{
    font-size:38px;
    color:var(--green);
    margin:10px 0;
}

.muted{
    color:var(--muted);
    line-height:1.7;
}

.error{
    display:none;
    background:#300b15;
    color:#ff91a8;
    padding:15px;
    border-radius:10px;
    margin-top:12px;
    white-space:pre-wrap;
    word-break:break-word;
}

.success{
    display:none;
    background:#062419;
    color:var(--green);
    padding:15px;
    border-radius:10px;
    margin-top:12px;
}

.warning{
    color:var(--yellow);
}

table{
    width:100%;
    border-collapse:collapse;
}

th,
td{
    padding:10px;
    border-bottom:1px solid var(--line);
    text-align:left;
}

th{
    color:var(--cyan);
}

.rank{
    color:var(--green);
    font-weight:800;
}

.chart-wrap{
    position:relative;
    height:320px;
    width:100%;
}

</style>

</head>

<body>

<header>

<div class="brand">
NEXO // HISTORICAL ANALYTICS v6
</div>

<div class="status">
● ANALYTICS ONLINE // ADAPTIVE ENGINE READY
</div>

</header>

<main>

<section class="card">

<h2>🎯 SELECT MARKET</h2>

<select id="market">

<option value="kalyan">
Kalyan
</option>

<option value="main-bazar">
Main Bazar
</option>

</select>

<button
id="fetchBtn"
onclick="syncData()">

⚡ FETCH PANEL HISTORY

</button>

<div
id="success"
class="success">
</div>

<div
id="error"
class="error">
</div>

</section>


<section class="card">

<h2>📦 DATABASE</h2>

<div
id="records"
class="metric">
—
</div>

<div class="muted">
Accumulated historical observations
</div>

</section>


<section class="card">

<h2>🧠 NEXO STATISTICAL RANKING</h2>

<div class="muted">

Frequency +
recent frequency +
recency decay +
momentum +
digit stability.

<br><br>

Historical statistical analysis only.

</div>

<br>

<table>

<thead>

<tr>
<th>#</th>
<th>Panel</th>
<th>Occurrences</th>
<th>Gap</th>
<th>Score</th>
</tr>

</thead>

<tbody id="ranking">
</tbody>

</table>

</section>


<section class="card">

<h2>📊 TOP PANEL FREQUENCY</h2>

<div class="chart-wrap">

<canvas id="chart"></canvas>

</div>

</section>


<section class="card">

<h2>🔢 DIGIT DISTRIBUTION</h2>

<table>

<thead>

<tr>
<th>Digit</th>
<th>Count</th>
</tr>

</thead>

<tbody id="digits">
</tbody>

</table>

</section>


<section class="card">

<h2>🧪 WALK-FORWARD BACKTEST</h2>

<div
id="backtest"
class="muted">

No backtest loaded.

</div>

</section>

</main>


<script>

let chart = null;


/* ==========================================================
   SAFE ERROR DISPLAY
   ========================================================== */

function showError(message){

    const box =
        document.getElementById("error");

    box.textContent =
        "FETCH ERROR: " +
        String(message);

    box.style.display =
        "block";

    document.getElementById(
        "success"
    ).style.display = "none";
}


function showSuccess(message){

    const box =
        document.getElementById("success");

    box.textContent =
        String(message);

    box.style.display =
        "block";

    document.getElementById(
        "error"
    ).style.display = "none";
}


/* ==========================================================
   CLEAR
   ========================================================== */

function clearResults(){

    document.getElementById(
        "ranking"
    ).innerHTML = "";

    document.getElementById(
        "digits"
    ).innerHTML = "";

    document.getElementById(
        "backtest"
    ).textContent =
        "Loading...";
}


/* ==========================================================
   ROBUST RESPONSE PARSER
   ========================================================== */

async function readJsonResponse(response){

    const text =
        await response.text();

    if(!text){

        throw new Error(
            "Server returned an empty response. HTTP " +
            response.status
        );
    }

    try{

        return JSON.parse(text);

    }catch(parseError){

        /*
         Safari often reports:
         "The string did not match the expected pattern."
        when JSON parsing fails.

        Show the actual server response instead.
        */

        const preview =
            text
            .replace(/\s+/g, " ")
            .slice(0, 500);

        throw new Error(
            "Server returned invalid JSON. " +
            "HTTP " +
            response.status +
            ". Response: " +
            preview
        );
    }
}


/* ==========================================================
   SYNC
   ========================================================== */

async function syncData(){

    const button =
        document.getElementById(
            "fetchBtn"
        );

    const market =
        document.getElementById(
            "market"
        ).value;

    button.disabled = true;

    button.textContent =
        "⏳ FETCHING...";

    clearResults();

    try{

        /*
         Same-origin relative URL.
         This avoids malformed absolute URLs.
        */

        const endpoint =
            new URL(
                "/api/sync",
                window.location.origin
            ).toString();

        const response =
            await fetch(
                endpoint,
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json",
                        "Accept":
                            "application/json"
                    },

                    body:JSON.stringify({
                        market:market
                    })
                }
            );

        const data =
            await readJsonResponse(
                response
            );

        if(
            !response.ok ||
            !data.ok
        ){

            throw new Error(
                data.error ||
                "Server rejected the sync request."
            );
        }

        document.getElementById(
            "records"
        ).textContent =
            data.database_records ??
            data.analysis.records ??
            0;

        showSuccess(
            data.market +
            " sync complete — " +
            data.fetched_panels +
            " panels fetched; " +
            data.database_records +
            " total observations stored."
        );

        renderRanking(
            data.analysis.ranking
        );

        renderDigits(
            data.analysis.digit_frequency
        );

        renderChart(
            data.analysis.top_panels
        );

        renderBacktest(
            data.analysis.backtest
        );

    }catch(error){

        console.error(
            "NEXO SYNC ERROR:",
            error
        );

        showError(
            error &&
            error.message
                ? error.message
                : String(error)
        );

        document.getElementById(
            "backtest"
        ).textContent =
            "No backtest available.";

    }finally{

        button.disabled = false;

        button.textContent =
            "⚡ FETCH PANEL HISTORY";
    }
}


/* ==========================================================
   RANKING
   ========================================================== */

function renderRanking(rows){

    const tbody =
        document.getElementById(
            "ranking"
        );

    if(
        !Array.isArray(rows) ||
        rows.length === 0
    ){

        tbody.innerHTML =
            `<tr>
                <td colspan="5">
                    No historical panels found.
                </td>
            </tr>`;

        return;
    }

    tbody.innerHTML =
        rows.map(
            (x, i) => `
            <tr>

                <td class="rank">
                    ${i + 1}
                </td>

                <td class="rank">
                    ${escapeHtml(x.panel)}
                </td>

                <td>
                    ${Number(x.frequency || 0)}
                </td>

                <td>
                    ${Number(x.gap || 0)}
                </td>

                <td>
                    ${Number(
                        x.score || 0
                    ).toFixed(2)}
                </td>

            </tr>
            `
        ).join("");
}


/* ==========================================================
   DIGITS
   ========================================================== */

function renderDigits(rows){

    const tbody =
        document.getElementById(
            "digits"
        );

    if(
        !Array.isArray(rows) ||
        rows.length === 0
    ){

        tbody.innerHTML =
            `<tr>
                <td colspan="2">
                    No digit data.
                </td>
            </tr>`;

        return;
    }

    tbody.innerHTML =
        rows.map(
            x => `
            <tr>

                <td>
                    ${escapeHtml(x.digit)}
                </td>

                <td>
                    ${Number(x.count || 0)}
                </td>

            </tr>
            `
        ).join("");
}


/* ==========================================================
   CHART
   ========================================================== */

function renderChart(rows){

    if(!Array.isArray(rows)){
        return;
    }

    const labels =
        rows.map(
            x => x.value
        );

    const values =
        rows.map(
            x => Number(
                x.count || 0
            )
        );

    if(chart){
        chart.destroy();
        chart = null;
    }

    const canvas =
        document.getElementById(
            "chart"
        );

    if(!canvas){
        return;
    }

    chart =
        new Chart(
            canvas,
            {
                type:"bar",

                data:{
                    labels:labels,

                    datasets:[
                        {
                            label:
                                "Historical occurrences",

                            data:values,

                            backgroundColor:
                                "#00e5ff99",

                            borderColor:
                                "#00e5ff",

                            borderWidth:1
                        }
                    ]
                },

                options:{
                    responsive:true,

                    maintainAspectRatio:false,

                    plugins:{
                        legend:{
                            labels:{
                                color:
                                    "#dffcff"
                            }
                        }
                    },

                    scales:{
                        x:{
                            ticks:{
                                color:
                                    "#8195ac"
                            }
                        },

                        y:{
                            ticks:{
                                color:
                                    "#8195ac"
                            }
                        }
                    }
                }
            }
        );
}


/* ==========================================================
   BACKTEST
   ========================================================== */

function renderBacktest(result){

    const box =
        document.getElementById(
            "backtest"
        );

    if(
        !result ||
        !result.available
    ){

        box.innerHTML =
            `<span class="warning">
                ${escapeHtml(
                    result?.message ||
                    "Not enough historical data."
                )}
            </span>`;

        return;
    }

    box.innerHTML = `
        Trials:
        <strong>
            ${Number(result.trials || 0)}
        </strong>

        <br>

        Top-10 historical hits:
        <strong>
            ${Number(
                result.top10_hits || 0
            )}
        </strong>

        <br>

        Historical hit rate:
        <strong>
            ${Number(
                result.hit_rate || 0
            ).toFixed(2)}%
        </strong>

        <br><br>

        <span class="warning">
            ${escapeHtml(
                result.warning ||
                "Historical backtest only."
            )}
        </span>
    `;
}


/* ==========================================================
   HTML ESCAPE
   ========================================================== */

function escapeHtml(value){

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


/* ==========================================================
   DATABASE STATUS
   ========================================================== */

async function loadDatabase(){

    try{

        const endpoint =
            new URL(
                "/api/database",
                window.location.origin
            ).toString();

        const response =
            await fetch(
                endpoint,
                {
                    headers:{
                        "Accept":
                            "application/json"
                    }
                }
            );

        const data =
            await readJsonResponse(
                response
            );

        if(
            data &&
            data.ok
        ){

            document.getElementById(
                "records"
            ).textContent =
                data.records ?? 0;
        }

    }catch(error){

        console.log(
            "Database status unavailable:",
            error
        );
    }
}


/* ==========================================================
   START
   ========================================================== */

loadDatabase();

</script>

</body>
</html>
"""


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():
    return render_template_string(
        HTML
    )


# ============================================================
# START
# ============================================================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
