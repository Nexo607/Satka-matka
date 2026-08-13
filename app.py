from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request

import analytics
import adaptive_engine


# ============================================================
# NEXO // HISTORICAL ANALYTICS v6
# ============================================================

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "nexo.db"


# ============================================================
# CONFIGURATION
# ============================================================

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
        "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(str(DATABASE))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            result_date TEXT,
            update_time TEXT,
            sequence INTEGER DEFAULT 1,
            panel TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_market
        ON observations(market)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_observations_date
        ON observations(market, result_date, update_time)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            source TEXT NOT NULL,
            rows_found INTEGER NOT NULL,
            panels_found INTEGER NOT NULL,
            inserted INTEGER NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SOURCE VALIDATION
# ============================================================

def valid_source(url: str) -> bool:
    return url in {
        item["url"]
        for item in MARKETS.values()
    }


def fetch_source(url: str) -> str:

    if not valid_source(url):
        raise ValueError(
            "Unknown historical source."
        )

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError(
            "Source must use HTTPS."
        )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )

    response.raise_for_status()

    if not response.text:
        raise ValueError(
            "Source returned an empty page."
        )

    return response.text


# ============================================================
# VALUE PARSING
# ============================================================

def normalize_panel(value: str) -> str | None:

    digits = re.findall(
        r"\d",
        value
    )

    if len(digits) != 3:
        return None

    return "".join(digits)


def extract_panels_from_cell(
    cell
) -> list[str]:

    text = cell.get_text(
        " ",
        strip=True
    )

    if not text:
        return []

    if "*" in text:
        return []

    result = []

    matches = re.findall(
        r"(?<!\d)(\d)\s+(\d)\s+(\d)(?!\d)",
        text
    )

    for a, b, c in matches:

        panel = f"{a}{b}{c}"

        if len(panel) == 3:
            result.append(panel)

    if not matches:

        compact = re.sub(
            r"\s+",
            "",
            text
        )

        match = re.fullmatch(
            r"\d{3}",
            compact
        )

        if match:
            result.append(compact)

    return result


# ============================================================
# DATE / TIME EXTRACTION
# ============================================================

DATE_PATTERNS = [
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
]

TIME_PATTERN = r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?\b"


def extract_date(text: str) -> str:

    for pattern in DATE_PATTERNS:

        match = re.search(
            pattern,
            text
        )

        if match:
            return match.group(0)

    return ""


def extract_time(text: str) -> str:

    match = re.search(
        TIME_PATTERN,
        text
    )

    if not match:
        return ""

    return match.group(0)


def normalize_date(value: str) -> str:

    value = value.strip()

    if not value:
        return ""

    formats = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%Y-%m-%d",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            ).strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            pass

    return value


def normalize_time(value: str) -> str:

    value = value.strip()

    if not value:
        return ""

    formats = [
        "%H:%M",
        "%H:%M:%S",
        "%I:%M %p",
        "%I:%M:%S %p",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                value,
                fmt
            ).strftime(
                "%H:%M"
            )

        except ValueError:
            pass

    return value


# ============================================================
# HISTORICAL HTML PARSER
# ============================================================

def parse_observations(
    html: str
) -> tuple[list[dict[str, Any]], int]:

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    observations = []
    rows_found = 0

    for table in soup.find_all("table"):

        sequence = 0

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

            if not row_text:
                continue

            lower = row_text.lower()

            if (
                "date" in lower
                and (
                    "mon" in lower
                    or "monday" in lower
                )
            ):
                continue

            result_date = normalize_date(
                extract_date(row_text)
            )

            update_time = normalize_time(
                extract_time(row_text)
            )

            found = []

            # First cell normally contains date.
            # Remaining cells contain results.
            data_cells = cells[1:] if len(cells) > 1 else cells

            for cell in data_cells:

                found.extend(
                    extract_panels_from_cell(
                        cell
                    )
                )

            if not found:
                continue

            rows_found += 1

            for panel in found:

                sequence += 1

                observations.append({
                    "result_date":
                        result_date,

                    "update_time":
                        update_time,

                    "sequence":
                        sequence,

                    "value":
                        panel,
                })

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    if not observations:

        text = soup.get_text(
            "\n",
            strip=True
        )

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        sequence = 0

        for line in lines:

            compact = re.sub(
                r"\s+",
                "",
                line
            )

            if re.fullmatch(
                r"\d{3}",
                compact
            ):

                sequence += 1

                observations.append({
                    "result_date": "",
                    "update_time": "",
                    "sequence": sequence,
                    "value": compact,
                })

    return observations, rows_found


# ============================================================
# DATABASE INSERT
# ============================================================

def observation_exists(
    conn,
    market: str,
    observation: dict[str, Any]
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM observations
        WHERE market = ?
          AND panel = ?
          AND COALESCE(result_date, '') = ?
          AND COALESCE(update_time, '') = ?
          AND sequence = ?
        LIMIT 1
        """,
        (
            market,
            observation["value"],
            observation.get(
                "result_date",
                ""
            ),
            observation.get(
                "update_time",
                ""
            ),
            int(
                observation.get(
                    "sequence",
                    1
                ) or 1
            ),
        ),
    ).fetchone()

    return row is not None


def save_observations(
    market: str,
    observations: list[dict[str, Any]],
    source: str
) -> int:

    now = datetime.now(
        timezone.utc
    ).isoformat()

    conn = db()

    inserted = 0

    for observation in observations:

        try:

            if observation_exists(
                conn,
                market,
                observation
            ):
                continue

            conn.execute(
                """
                INSERT INTO observations
                (
                    market,
                    result_date,
                    update_time,
                    sequence,
                    panel,
                    source,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market,
                    observation.get(
                        "result_date",
                        ""
                    ),
                    observation.get(
                        "update_time",
                        ""
                    ),
                    int(
                        observation.get(
                            "sequence",
                            1
                        ) or 1
                    ),
                    observation["value"],
                    source,
                    now,
                ),
            )

            inserted += 1

        except sqlite3.Error:
            continue

    conn.commit()

    conn.execute(
        """
        INSERT INTO sync_log
        (
            market,
            source,
            rows_found,
            panels_found,
            inserted,
            synced_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            market,
            source,
            0,
            len(observations),
            inserted,
            now,
        ),
    )

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# LOAD COMPLETE MARKET HISTORY
# ============================================================

def load_market_records(
    market: str
) -> list[dict[str, Any]]:

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            market,
            result_date,
            update_time,
            sequence,
            panel
        FROM observations
        WHERE market = ?
        ORDER BY
            COALESCE(result_date, ''),
            COALESCE(update_time, ''),
            sequence,
            id
        """,
        (market,),
    ).fetchall()

    conn.close()

    records = []

    for row in rows:

        records.append({
            "id":
                row["id"],

            "value":
                row["panel"],

            "result_date":
                row["result_date"] or "",

            "update_time":
                row["update_time"] or "",

            "sequence":
                row["sequence"] or 1,
        })

    return records


# ============================================================
# COMPATIBILITY: LEGACY DATABASE
# ============================================================

def migrate_legacy_database():

    conn = db()

    tables = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            """
        ).fetchall()
    }

    # Nothing to migrate.
    if "panels" not in tables:
        conn.close()
        return

    # New table already exists. Keep legacy table untouched.
    # We intentionally do not copy legacy rows because the old
    # schema lost dates/times and incorrectly deduplicated panels.
    conn.close()


# ============================================================
# V6 ANALYSIS
# ============================================================

def run_v6_analysis(
    market: str,
    records: list[dict],
    recent_window: int = 20
) -> dict[str, Any]:

    if not records:

        return {
            "observations": 0,
            "ranking": [],
            "time_slots": {},
            "position_distribution": {},
            "stability": {},
            "sequence": [],
            "adaptive": None,
        }

    analysis_result = analytics.analyze_market(
        records,
        recent_window=recent_window
    )

    adaptive_result = adaptive_engine.auto_update(
        market=market,
        records=records,
        minimum_new_records=5,
        min_history=10,
        top_n=10,
        recent_window=recent_window,
        trials=60,
    )

    analysis_result["adaptive"] = (
        adaptive_result
    )

    return analysis_result


# ============================================================
# API: MARKETS
# ============================================================

@app.get("/api/markets")
def markets():

    return jsonify([
        {
            "id": key,
            "name": value["name"],
        }
        for key, value in MARKETS.items()
    ])


# ============================================================
# API: SYNC
# ============================================================

@app.post("/api/sync")
def sync():

    try:

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

        html = fetch_source(
            source
        )

        observations, rows_found = (
            parse_observations(
                html
            )
        )

        if not observations:

            return jsonify({
                "ok": False,
                "error":
                    "The source page was reached, "
                    "but no historical panel records "
                    "were detected."
            }), 502

        # Save complete observations.
        inserted = save_observations(
            market,
            observations,
            source,
        )

        # IMPORTANT:
        # Analyze accumulated database history,
        # not only the current HTTP response.
        records = load_market_records(
            market
        )

        analysis_result = run_v6_analysis(
            market,
            records,
            recent_window=20
        )

        return jsonify({

            "ok": True,

            "market":
                MARKETS[
                    market
                ]["name"],

            "source":
                source,

            "rows_found":
                rows_found,

            "fetched_panels":
                len(observations),

            "new_records":
                inserted,

            "total_historical_records":
                len(records),

            "analysis":
                analysis_result,
        })

    except requests.Timeout:

        return jsonify({
            "ok": False,
            "error":
                "Source request timed out. Try again."
        }), 504

    except requests.RequestException as exc:

        app.logger.exception(
            "SOURCE REQUEST ERROR"
        )

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
                str(exc)
        }), 500


# ============================================================
# API: DATABASE
# ============================================================

@app.get("/api/database")
def database_stats():

    conn = db()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM observations
        """
    ).fetchone()[0]

    unique = conn.execute(
        """
        SELECT COUNT(DISTINCT panel)
        FROM observations
        """
    ).fetchone()[0]

    markets_count = conn.execute(
        """
        SELECT COUNT(DISTINCT market)
        FROM observations
        """
    ).fetchone()[0]

    conn.close()

    return jsonify({
        "records": total,
        "unique": unique,
        "markets": markets_count,
    })


# ============================================================
# API: MARKET HISTORY
# ============================================================

@app.get("/api/history/<market>")
def history(market: str):

    market = market.strip().lower()

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error":
                "Unknown market."
        }), 400

    records = load_market_records(
        market
    )

    return jsonify({
        "ok": True,
        "market":
            MARKETS[
                market
            ]["name"],
        "records":
            records,
        "count":
            len(records),
    })


# ============================================================
# API: ANALYSIS
# ============================================================

@app.get("/api/analysis/<market>")
def analysis_api(market: str):

    market = market.strip().lower()

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error":
                "Unknown market."
        }), 400

    records = load_market_records(
        market
    )

    result = run_v6_analysis(
        market,
        records,
        recent_window=20
    )

    return jsonify({
        "ok": True,
        "market":
            MARKETS[
                market
            ]["name"],
        "analysis":
            result,
    })


# ============================================================
# API: ADAPTIVE MODEL STATUS
# ============================================================

@app.get("/api/model/<market>")
def model_status(market: str):

    market = market.strip().lower()

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error":
                "Unknown market."
        }), 400

    return jsonify(
        adaptive_engine.get_model_status(
            market
        )
    )


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
    padding:22px;
    border-bottom:1px solid var(--line);
}

.brand{
    color:var(--cyan);
    font-size:21px;
    font-weight:900;
    letter-spacing:.08em;
}

.status{
    margin-top:10px;
    color:var(--green);
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

.model-grid{
    display:grid;
    grid-template-columns:
      repeat(2,minmax(0,1fr));
    gap:10px;
}

.model-box{
    border:1px solid var(--line);
    border-radius:10px;
    padding:12px;
}

.model-label{
    color:var(--muted);
    font-size:11px;
}

.model-value{
    color:var(--green);
    font-size:18px;
    margin-top:5px;
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
}

.success{
    display:none;
    background:#062419;
    color:var(--green);
    padding:15px;
    border-radius:10px;
    margin-top:12px;
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
}

.warning{
    color:var(--yellow);
}

@media(max-width:600px){

    .model-grid{
        grid-template-columns:1fr;
    }

    th,
    td{
        padding:8px 5px;
        font-size:12px;
    }

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

<button id="fetchBtn"
        onclick="syncData()">

⚡ FETCH PANEL HISTORY

</button>

<div id="success"
     class="success">
</div>

<div id="error"
     class="error">
</div>

</section>


<section class="card">

<h2>📦 DATABASE</h2>

<div id="records"
     class="metric">
—
</div>

<div class="muted">
Accumulated historical observations
</div>

</section>


<section class="card">

<h2>🧠 ADAPTIVE ENGINE v6</h2>

<div class="model-grid">

<div class="model-box">

<div class="model-label">
MODEL STATUS
</div>

<div id="modelStatus"
     class="model-value">
—
</div>

</div>

<div class="model-box">

<div class="model-label">
TRAINING RUNS
</div>

<div id="trainingRuns"
     class="model-value">
—
</div>

</div>

<div class="model-box">

<div class="model-label">
OBSERVATIONS USED
</div>

<div id="modelObservations"
     class="model-value">
—
</div>

</div>

<div class="model-box">

<div class="model-label">
VALIDATED SCORE
</div>

<div id="validatedScore"
     class="model-value">
—
</div>

</div>

</div>

<br>

<div class="muted">
Weights are calibrated using historical
walk-forward evaluation.
</div>

</section>


<section class="card">

<h2>🧠 NEXO V6 STATISTICAL RANKING</h2>

<div class="muted">

Frequency + recent frequency +
recency decay + momentum +
gap + repetition.

<br><br>

Historical/statistical analysis only.
Scores do not guarantee future outcomes.

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

<div id="backtest"
     class="muted">

No backtest loaded.

</div>

</section>

</main>


<script>

let chart = null;


function showError(message){

    const box =
      document.getElementById("error");

    box.textContent =
      "FETCH ERROR: " + message;

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
      message;

    box.style.display =
      "block";

    document.getElementById(
      "error"
    ).style.display = "none";

}


function clearResults(){

    document.getElementById(
      "ranking"
    ).innerHTML = "";

    document.getElementById(
      "digits"
    ).innerHTML = "";

    document.getElementById(
      "backtest"
    ).innerHTML =
      "Loading...";

}


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
      "⏳ FETCHING + ANALYZING...";

    clearResults();

    try{

        const response =
          await fetch(
            "/api/sync",
            {
                method:"POST",

                headers:{
                    "Content-Type":
                      "application/json"
                },

                body:JSON.stringify({
                    market:market
                })
            }
          );

        const data =
          await response.json();

        if(!response.ok ||
           !data.ok){

            throw new Error(
              data.error ||
              "Unable to fetch history."
            );

        }

        document.getElementById(
          "records"
        ).textContent =
          data.total_historical_records;

        showSuccess(
          data.market +
          " sync complete — " +
          data.new_records +
          " new observations added. Total: " +
          data.total_historical_records
        );

        const analysis =
          data.analysis;

        renderRanking(
          analysis.ranking
        );

        renderDigits(
          buildDigitRows(
            analysis.position_distribution
          )
        );

        renderChart(
          analysis.ranking
        );

        renderBacktest(
          analysis
        );

        renderAdaptive(
          analysis.adaptive
        );

    }catch(error){

        showError(
          error.message
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


function renderRanking(rows){

    const tbody =
      document.getElementById(
        "ranking"
      );

    if(!rows ||
       rows.length === 0){

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
        (x,i) => `
        <tr>

          <td class="rank">
            ${i + 1}
          </td>

          <td class="rank">
            ${escapeHtml(
              x.value
            )}
          </td>

          <td>
            ${x.frequency}
          </td>

          <td>
            ${x.gap ?? "—"}
          </td>

          <td>
            ${Number(
              x.adaptive_score ??
              x.score ??
              0
            ).toFixed(4)}
          </td>

        </tr>
        `
      ).join("");

}


function buildDigitRows(distribution){

    const counter = {};

    if(!distribution){
        return [];
    }

    Object.values(
      distribution
    ).forEach(
      position => {

        Object.entries(
          position
        ).forEach(
          ([digit,count]) => {

            counter[digit] =
              (counter[digit] || 0)
              + count;

          }
        );

      }
    );

    return Object.entries(
      counter
    )
    .sort(
      (a,b) =>
        Number(a[0]) -
        Number(b[0])
    )
    .map(
      ([digit,count]) => ({
        digit,
        count
      })
    );

}


function renderDigits(rows){

    document.getElementById(
      "digits"
    ).innerHTML =
      rows.map(
        x => `
        <tr>

          <td>
            ${escapeHtml(
              x.digit
            )}
          </td>

          <td>
            ${x.count}
          </td>

        </tr>
        `
      ).join("");

}


function renderChart(rows){

    const labels =
      (rows || [])
      .slice(0,10)
      .map(
        x =>
          x.value
      );

    const values =
      (rows || [])
      .slice(0,10)
      .map(
        x =>
          x.frequency
      );

    if(chart){
        chart.destroy();
    }

    chart =
      new Chart(
        document.getElementById(
          "chart"
        ),
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
                  color:"#dffcff"
                }
              }
            },

            scales:{
              x:{
                ticks:{
                  color:"#8195ac"
                }
              },

              y:{
                ticks:{
                  color:"#8195ac"
                }
              }
            }
          }
        }
      );

}


function renderBacktest(analysis){

    const box =
      document.getElementById(
        "backtest"
      );

    const result =
      analysis?.backtest;

    if(!result ||
       !result.available){

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
        ${result.trials}
      </strong>

      <br>

      Top-10 historical hits:
      <strong>
        ${result.top10_hits}
      </strong>

      <br>

      Historical hit rate:
      <strong>
        ${Number(
          result.hit_rate
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


function renderAdaptive(adaptive){

    if(!adaptive){
        return;
    }

    const training =
      adaptive.training || {};

    const model =
      adaptive.analysis || {};

    const state =
      model.model_status ||
      "unknown";

    document.getElementById(
      "modelStatus"
    ).textContent =
      state;

    document.getElementById(
      "trainingRuns"
    ).textContent =
      model.training_runs ??
      "—";

    document.getElementById(
      "modelObservations"
    ).textContent =
      model.observations ??
      "—";

    document.getElementById(
      "validatedScore"
    ).textContent =
      model.trained_score ??
      "—";
}


function escapeHtml(value){

    return String(value)
      .replaceAll(
        "&",
        "&amp;"
      )
      .replaceAll(
        "<",
        "&lt;"
      )
      .replaceAll(
        ">",
        "&gt;"
      )
      .replaceAll(
        '"',
        "&quot;"
      )
      .replaceAll(
        "'",
        "&#039;"
      );

}


async function loadDatabase(){

    try{

        const response =
          await fetch(
            "/api/database"
          );

        const data =
          await response.json();

        document.getElementById(
          "records"
        ).textContent =
          data.records;

    }catch(error){

        console.log(
          "Database status unavailable"
        );

    }

}


async function loadModel(){

    try{

        const market =
          document.getElementById(
            "market"
          ).value;

        const response =
          await fetch(
            "/api/model/" +
            encodeURIComponent(
              market
            )
          );

        const data =
          await response.json();

        if(!data.ok){
            return;
        }

        const model =
          data.model || {};

        document.getElementById(
          "modelStatus"
        ).textContent =
          model.status || "untrained";

        document.getElementById(
          "trainingRuns"
        ).textContent =
          model.training_runs ?? 0;

        document.getElementById(
          "modelObservations"
        ).textContent =
          model.observations_used ?? 0;

        document.getElementById(
          "validatedScore"
        ).textContent =
          model.score ?? 0;

    }catch(error){

        console.log(
          "Model status unavailable"
        );

    }

}


document.getElementById(
  "market"
).addEventListener(
  "change",
  loadModel
);


loadDatabase();
loadModel();

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
migrate_legacy_database()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
