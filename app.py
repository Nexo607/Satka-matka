import re
import sqlite3
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request

import adaptive_engine

app = Flask(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

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
        "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
            source TEXT NOT NULL,
            rows_found INTEGER NOT NULL,
            panels_found INTEGER NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SECURITY / URL VALIDATION
# ============================================================

def valid_source(url):
    """
    Only allow the two known historical sources.
    """
    return url in {x["url"] for x in MARKETS.values()}


# ============================================================
# SOURCE FETCHING
# ============================================================

def fetch_source(url):
    if not valid_source(url):
        raise ValueError("Unknown historical source.")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ValueError("Source must use HTTPS.")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=25,
    )

    response.raise_for_status()

    if not response.text:
        raise ValueError("Source returned an empty page.")

    return response.text


# ============================================================
# PANEL PARSER
# ============================================================

def normalize_panel(value):
    """
    Converts things like:

        7 8 0
        7\\n8\\n0
        780

    into:

        780
    """

    digits = re.findall(r"\d", value)

    if len(digits) != 3:
        return None

    return "".join(digits)


def extract_panels_from_cell(cell):
    """
    Extract three-digit panel values from a table cell.
    """

    text = cell.get_text(" ", strip=True)

    if not text:
        return []

    if "*" in text:
        return []

    matches = re.findall(
        r"(?<!\d)(\d)\s+(\d)\s+(\d)(?!\d)",
        text
    )

    result = []

    for a, b, c in matches:
        panel = f"{a}{b}{c}"

        if len(panel) == 3:
            result.append(panel)

    if not matches:
        compact = re.sub(r"\s+", "", text)

        if re.fullmatch(r"\d{3}", compact):
            result.append(compact)

    return result


def parse_panels(html):
    """
    Parse historical panel values from HTML tables.

    Returns:
        panels = list of unique chronological observations
        rows_found = number of candidate table rows
    """

    soup = BeautifulSoup(html, "html.parser")

    panels = []
    rows_found = 0

    for table in soup.find_all("table"):

        for tr in table.find_all("tr"):

            cells = tr.find_all(["td", "th"])

            if not cells:
                continue

            row_text = tr.get_text(
                " ",
                strip=True
            )

            if "Date" in row_text and "Mon" in row_text:
                continue

            found_in_row = []

            for cell in cells[1:]:

                found_in_row.extend(
                    extract_panels_from_cell(cell)
                )

            if found_in_row:

                rows_found += 1

                panels.extend(
                    found_in_row
                )

    if not panels:

        text = soup.get_text(
            "\n",
            strip=True
        )

        lines = [
            x.strip()
            for x in text.splitlines()
            if x.strip()
        ]

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

                panels.append(
                    compact
                )

    return panels, rows_found


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

        try:

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO panels
                (market, panel, source, fetched_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    market,
                    panel,
                    source,
                    now,
                ),
            )

            if cursor.rowcount:
                inserted += 1

        except sqlite3.Error:
            continue

    conn.commit()

    conn.execute(
        """
        INSERT INTO sync_log
        (market, source, rows_found, panels_found, synced_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            market,
            source,
            len(panels),
            inserted,
            now,
        ),
    )

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# STATISTICAL ENGINE
# ============================================================

def minmax(values):

    if not values:
        return {}

    lo = min(values)
    hi = max(values)

    if hi == lo:
        return {
            k: 0.5
            for k in range(len(values))
        }

    return {
        i: (v - lo) / (hi - lo)
        for i, v in enumerate(values)
    }


def percentile_rank(
    value,
    values
):

    if not values:
        return 0.0

    less_equal = sum(
        v <= value
        for v in values
    )

    return (
        less_equal /
        len(values)
    )


def analyze_panels(panels):

    if not panels:

        return {
            "records": 0,
            "unique": 0,
            "ranking": [],
            "digit_frequency": [],
            "top_panels": [],
            "backtest": None,
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

    candidates = sorted(
        counter.keys()
    )

    raw = []

    for panel in candidates:

        frequency = counter[
            panel
        ]

        recent_frequency = (
            recent_counter[
                panel
            ]
        )

        last_index = last_seen[
            panel
        ]

        gap = (
            total -
            1 -
            last_index
        )

        decay = math.exp(
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
            int(x)
            for x in panel
        ]

        mean_digit = statistics.mean(
            digits
        )

        try:

            digit_variance = (
                statistics.pvariance(
                    digits
                )
            )

        except statistics.StatisticsError:

            digit_variance = 0.0

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
            "recent_frequency": recent_frequency,
            "gap": gap,
            "recency": decay,
            "momentum": momentum,
            "digit_stability":
                digit_stability,
            "mean_digit":
                mean_digit,
        })

    def values(key):

        return [
            float(x[key])
            for x in raw
        ]

    feature_names = [
        "frequency",
        "recent_frequency",
        "recency",
        "momentum",
        "digit_stability",
    ]

    normalized = {}

    for key in feature_names:

        vals = values(
            key
        )

        lo = min(vals)
        hi = max(vals)

        for i, item in enumerate(
            raw
        ):

            if hi == lo:

                normalized[
                    (i, key)
                ] = 0.5

            else:

                normalized[
                    (i, key)
                ] = (
                    (item[key] - lo)
                    /
                    (hi - lo)
                )

    weights = {
        "frequency": 0.30,
        "recent_frequency": 0.20,
        "recency": 0.15,
        "momentum": 0.15,
        "digit_stability": 0.20,
    }

    ranking = []

    for i, item in enumerate(
        raw
    ):

        score = sum(
            normalized[
                (i, key)
            ] * weight
            for key, weight
            in weights.items()
        )

        ranking.append({
            **item,
            "score": round(
                score * 100,
                2
            ),
        })

    ranking.sort(
        key=lambda x: (
            -x["score"],
            -x["frequency"],
            x["gap"],
        )
    )

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
                digit_counter[digit],
        }
        for digit
        in sorted(
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
                "value": x["panel"],
                "count":
                    x["frequency"],
            }
            for x in ranking[:20]
        ],

        "digit_frequency":
            digit_frequency,

        "backtest":
            simple_backtest(
                panels
            ),
    }


# ============================================================
# WALK-FORWARD BACKTEST
# ============================================================

def simple_backtest(
    panels
):

    if len(panels) < 30:

        return {
            "available": False,
            "message":
                "Need at least 30 historical observations.",
        }

    hits = 0
    trials = 0

    start = max(
        20,
        len(panels) - 250
    )

    for i in range(
        start,
        len(panels)
    ):

        train = panels[:i]

        if len(
            set(train)
        ) < 10:

            continue

        result = (
            analyze_panels_without_backtest(
                train
            )
        )

        top10 = {
            x["panel"]
            for x in result[
                "ranking"
            ][:10]
        }

        actual = panels[i]

        if actual in top10:
            hits += 1

        trials += 1

    rate = (
        hits / trials
        if trials
        else 0
    )

    return {
        "available":
            trials > 0,

        "trials":
            trials,

        "top10_hits":
            hits,

        "hit_rate":
            round(
                rate * 100,
                2
            ),

        "warning":
            "Historical backtest only; "
            "not a guarantee of future outcomes.",
    }


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
            total -
            1 -
            last_seen[
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
            int(x)
            for x in panel
        ]

        variance = (
            statistics.pvariance(
                digits
            )
        )

        stability = (
            1 /
            (
                1 +
                variance
            )
        )

        raw.append({
            "panel": panel,
            "frequency": frequency,
            "recent_frequency":
                recent_frequency,
            "gap": gap,
            "recency":
                recency,
            "momentum":
                momentum,
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

    for key in features:

        vals = [
            x[key]
            for x in raw
        ]

        lo = min(vals)
        hi = max(vals)

        for i, value in enumerate(
            vals
        ):

            normalized[
                (i, key)
            ] = (
                0.5
                if hi == lo
                else (
                    (value - lo)
                    /
                    (hi - lo)
                )
            )

    weights = {
        "frequency": 0.30,
        "recent_frequency": 0.20,
        "recency": 0.15,
        "momentum": 0.15,
        "digit_stability": 0.20,
    }

    ranking = []

    for i, item in enumerate(
        raw
    ):

        score = sum(
            normalized[
                (i, key)
            ] *
            weights[key]
            for key in features
        )

        ranking.append({
            **item,
            "score":
                score * 100,
        })

    ranking.sort(
        key=lambda x: (
            -x["score"],
            -x["frequency"],
            x["gap"],
        )
    )

    return {
        "ranking":
            ranking
    }


# ============================================================
# API
# ============================================================

@app.get("/api/markets")
def markets():

    return jsonify([
        {
            "id": key,
            "name": value["name"],
        }
        for key, value
        in MARKETS.items()
    ])


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

        panels, rows_found = (
            parse_panels(
                html
            )
        )

        if not panels:

            return jsonify({
                "ok": False,
                "error":
                    "The source page was reached, "
                    "but no 3-digit panel records "
                    "were detected."
            }), 502

        inserted = save_panels(
            market,
            panels,
            source,
        )

        analysis = analyze_panels(
            panels
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
                len(panels),

            "new_records":
                inserted,

            "analysis":
                analysis,
        })

    except requests.Timeout:

        return jsonify({
            "ok": False,
            "error":
                "Source request timed out. Try again."
        }), 504

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
                str(exc)
        }), 500


@app.get("/api/database")
def database_stats():

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) FROM panels"
    ).fetchone()[0]

    unique = conn.execute(
        "SELECT COUNT(DISTINCT panel) FROM panels"
    ).fetchone()[0]

    conn.close()

    return jsonify({
        "records": total,
        "unique": unique,
    })


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

<title>NEXO v5 Historical Analytics</title>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>

:root{
    --bg:#05080d;
    --panel:#08121c;
    --cyan:#00e5ff;
    --green:#00ff88;
    --red:#ff5577;
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
    color:#ffcf66;
}

</style>

</head>

<body>

<header>

<div class="brand">
NEXO // HISTORICAL ANALYTICS v5
</div>

<div class="status">
● ANALYTICS ONLINE
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
Stored historical panel records
</div>

</section>


<section class="card">

<h2>🧠 NEXO STATISTICAL RANKING</h2>

<div class="muted">

Frequency + recent frequency +
recency decay + momentum +
digit stability.

<br><br>

These are historical statistical
rankings, not guaranteed future results.

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
      "⏳ FETCHING...";

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
          data.analysis.records;

        showSuccess(
          data.market +
          " sync complete — " +
          data.fetched_panels +
          " historical panels loaded."
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
            ${escapeHtml(x.panel)}
          </td>

          <td>
            ${x.frequency}
          </td>

          <td>
            ${x.gap}
          </td>

          <td>
            ${Number(x.score).toFixed(2)}
          </td>

        </tr>
        `
      ).join("");

}


function renderDigits(rows){

    document.getElementById(
      "digits"
    ).innerHTML =
      rows.map(
        x => `
        <tr>

          <td>
            ${escapeHtml(x.digit)}
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
      rows.map(
        x => x.value
      );

    const values =
      rows.map(
        x => x.count
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


function renderBacktest(result){

    const box =
      document.getElementById(
        "backtest"
      );

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
          result.warning
        )}
      </span>
    `;

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


loadDatabase();

</script>

</body>

</html>
"""


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
