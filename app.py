from flask import Flask, jsonify, render_template
from bs4 import BeautifulSoup
from collections import Counter
from datetime import datetime, timezone
import sqlite3
import requests
import hashlib
import math
import os
import random
import statistics
import re

app = Flask(__name__)

DB_PATH = os.environ.get("NEXO_DB", "nexo.db")

MARKETS = {
    "kalyan": {
        "name": "Kalyan",
        "url": "https://dpbossss.boston/panel-chart-record/kalyan.php"
    },
    "main_bazar": {
        "name": "Main Bazar",
        "url": "https://dpbossss.boston/panel-chart-record/main-bazar.php"
    }
}

HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "Version/17.0 Mobile/15E148 Safari/604.1",
    "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            panel TEXT NOT NULL,
            row_index INTEGER NOT NULL,
            column_index INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(
                market,
                source_hash,
                row_index,
                column_index
            )
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            source_hash TEXT,
            extracted INTEGER DEFAULT 0,
            inserted INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SOURCE
# ============================================================

def fetch_source(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=40
    )

    response.raise_for_status()

    if not response.text.strip():
        raise RuntimeError("Source returned empty HTML.")

    return response.text


# ============================================================
# PANEL PARSING
# ============================================================

def normalize_panel(text):
    """
    Converts source cell content into a 3-digit panel.

    Examples:

        '7 8 0' -> '780'
        '7\\n8\\n0' -> '780'

    2-digit Jodis are ignored.
    """

    digits = re.sub(
        r"[^0-9]",
        "",
        text or ""
    )

    if len(digits) != 3:
        return None

    return digits


def extract_panels(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    observations = []

    tables = soup.find_all("table")

    if not tables:
        raise RuntimeError(
            "No HTML table was found."
        )

    for table_index, table in enumerate(tables):

        rows = table.find_all("tr")

        for row_index, row in enumerate(rows):

            cells = row.find_all(
                ["td", "th"]
            )

            for column_index, cell in enumerate(cells):

                text = cell.get_text(
                    " ",
                    strip=True
                )

                panel = normalize_panel(text)

                if panel is None:
                    continue

                observations.append({
                    "panel": panel,
                    "row_index": (
                        table_index * 100000
                        + row_index
                    ),
                    "column_index": column_index
                })

    return observations


# ============================================================
# STORAGE
# ============================================================

def save_observations(
    market,
    source_hash,
    observations
):
    conn = db()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    inserted = 0

    for item in observations:

        cur = conn.execute(
            """
            INSERT OR IGNORE INTO observations
            (
                market,
                panel,
                row_index,
                column_index,
                source_hash,
                fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                item["panel"],
                item["row_index"],
                item["column_index"],
                source_hash,
                now
            )
        )

        inserted += int(cur.rowcount > 0)

    conn.commit()
    conn.close()

    return inserted


def load_values(market):
    conn = db()

    rows = conn.execute(
        """
        SELECT panel
        FROM observations
        WHERE market=?
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
# MATH HELPERS
# ============================================================

def percentile(values, value):
    """
    Percentile rank from 0 to 1.
    """

    if not values:
        return 0.5

    ordered = sorted(values)

    if len(ordered) == 1:
        return 0.5

    less = sum(
        x < value
        for x in ordered
    )

    equal = sum(
        x == value
        for x in ordered
    )

    return (
        less + 0.5 * equal
    ) / len(ordered)


def entropy(values):
    if not values:
        return 0.0

    counts = Counter(values)
    n = len(values)

    result = 0.0

    for count in counts.values():

        p = count / n

        result -= p * math.log2(p)

    return result


def safe_mean(values):
    return (
        sum(values) / len(values)
        if values
        else 0.0
    )


def recency_frequency(
    values,
    panel,
    decay=0.035
):
    """
    Exponential recency weighting.

    New observations receive larger weight.
    """

    if not values:
        return 0.0

    total_weight = 0.0
    panel_weight = 0.0

    for age, value in enumerate(
        reversed(values)
    ):

        weight = math.exp(
            -decay * age
        )

        total_weight += weight

        if value == panel:
            panel_weight += weight

    if total_weight == 0:
        return 0.0

    return panel_weight / total_weight


def digit_strength(
    values,
    panel
):
    if not values or len(panel) != 3:
        return 0.0

    positions = [
        Counter(),
        Counter(),
        Counter()
    ]

    for value in values:

        if len(value) != 3:
            continue

        for i in range(3):
            positions[i][value[i]] += 1

    n = len(values)

    scores = []

    for i in range(3):

        scores.append(
            positions[i][panel[i]] / n
        )

    return safe_mean(scores)


def momentum(
    values,
    panel
):
    if len(values) < 50:
        return 0.0

    recent = values[-25:]

    previous = values[-50:-25]

    r = (
        recent.count(panel)
        / len(recent)
    )

    p = (
        previous.count(panel)
        / len(previous)
    )

    return r - p


def stability(
    values,
    panel,
    window=25
):
    if len(values) < window:
        return 0.0

    blocks = []

    for start in range(
        0,
        len(values),
        window
    ):

        block = values[
            start:
            start + window
        ]

        if block:
            blocks.append(
                panel in block
            )

    if not blocks:
        return 0.0

    return sum(blocks) / len(blocks)


def gap(values, panel):
    try:
        reversed_index = (
            values[::-1].index(panel)
        )

        return reversed_index

    except ValueError:

        return len(values)


def drift_score(values):
    """
    Compares older and newer distributions.

    This is a regime-change indicator,
    not a prediction.
    """

    if len(values) < 100:
        return 0.0

    old = Counter(
        values[:-50]
    )

    new = Counter(
        values[-50:]
    )

    keys = set(old) | set(new)

    old_n = max(
        len(values[:-50]),
        1
    )

    new_n = max(
        len(values[-50:]),
        1
    )

    distance = 0.0

    for key in keys:

        p = old[key] / old_n
        q = new[key] / new_n

        distance += abs(p - q)

    return min(
        distance / 2,
        1
    )


# ============================================================
# FEATURE ENGINE
# ============================================================

def build_features(values):

    if not values:
        return []

    frequency = Counter(values)

    panels = list(
        frequency.keys()
    )

    recent25 = values[-25:]
    recent50 = values[-50:]
    recent100 = values[-100:]

    historical_frequency = []
    recent_frequency = []
    recent50_frequency = []
    recent100_frequency = []
    recency_values = []
    gap_values = []
    momentum_values = []
    digit_values = []
    stability_values = []
    entropy_values = []

    current_entropy = entropy(
        recent50
    )

    historical_entropy = entropy(
        values
    )

    entropy_change = (
        current_entropy -
        historical_entropy
    )

    drift = drift_score(
        values
    )

    for panel in panels:

        f = frequency[panel]

        rf25 = (
            recent25.count(panel)
            / max(len(recent25), 1)
        )

        rf50 = (
            recent50.count(panel)
            / max(len(recent50), 1)
        )

        rf100 = (
            recent100.count(panel)
            / max(len(recent100), 1)
        )

        rec = recency_frequency(
            values,
            panel
        )

        g = gap(
            values,
            panel
        )

        mom = momentum(
            values,
            panel
        )

        dig = digit_strength(
            values,
            panel
        )

        stab = stability(
            values,
            panel
        )

        historical_frequency.append(f)
        recent_frequency.append(rf25)
        recent50_frequency.append(rf50)
        recent100_frequency.append(rf100)
        recency_values.append(rec)
        gap_values.append(g)
        momentum_values.append(mom)
        digit_values.append(dig)
        stability_values.append(stab)
        entropy_values.append(
            abs(entropy_change)
        )

    # Percentile normalization
    features = []

    for i, panel in enumerate(panels):

        frequency_score = percentile(
            historical_frequency,
            historical_frequency[i]
        )

        recent_score = percentile(
            recent_frequency,
            recent_frequency[i]
        )

        recent50_score = percentile(
            recent50_frequency,
            recent50_frequency[i]
        )

        recent100_score = percentile(
            recent100_frequency,
            recent100_frequency[i]
        )

        recency_score = percentile(
            recency_values,
            recency_values[i]
        )

        momentum_score = percentile(
            momentum_values,
            momentum_values[i]
        )

        digit_score = percentile(
            digit_values,
            digit_values[i]
        )

        stability_score = percentile(
            stability_values,
            stability_values[i]
        )

        # Gap is deliberately given
        # limited influence.
        gap_score = percentile(
            gap_values,
            gap_values[i]
        )

        # Composite model.
        #
        # These are model scores,
        # NOT future-result probabilities.

        score = (

            0.20 *
            frequency_score +

            0.18 *
            recent_score +

            0.12 *
            recent50_score +

            0.08 *
            recent100_score +

            0.15 *
            recency_score +

            0.12 *
            momentum_score +

            0.08 *
            digit_score +

            0.05 *
            stability_score +

            0.02 *
            gap_score

        )

        features.append({

            "panel": panel,

            "score":
                round(
                    score * 100,
                    2
                ),

            "frequency":
                frequency[panel],

            "recent25":
                round(
                    recent_frequency[i] * 100,
                    3
                ),

            "recent50":
                round(
                    recent50_frequency[i] * 100,
                    3
                ),

            "recent100":
                round(
                    recent100_frequency[i] * 100,
                    3
                ),

            "recency":
                round(
                    recency_values[i] * 100,
                    3
                ),

            "momentum":
                round(
                    momentum_values[i] * 100,
                    3
                ),

            "digit_strength":
                round(
                    digit_values[i] * 100,
                    3
                ),

            "stability":
                round(
                    stability_values[i] * 100,
                    3
                ),

            "gap":
                gap(values, panel),

            "drift":
                round(
                    drift * 100,
                    2
                ),

            "entropy_change":
                round(
                    entropy_change,
                    5
                )

        })

    features.sort(
        key=lambda x:
            x["score"],
        reverse=True
    )

    return features


# ============================================================
# BASELINES
# ============================================================

def top_frequency(
    training,
    k=10
):
    return [
        panel
        for panel, _ in
        Counter(training).most_common(k)
    ]


def top_recent(
    training,
    k=10,
    window=50
):
    return [
        panel
        for panel, _ in
        Counter(
            training[-window:]
        ).most_common(k)
    ]


# ============================================================
# WALK-FORWARD BACKTEST
# ============================================================

def evaluate_candidates(
    candidates,
    testing
):

    if not testing:
        return 0.0

    hits = sum(
        value in candidates
        for value in testing
    )

    return (
        hits /
        len(testing) *
        100
    )


def walk_forward(
    values,
    minimum_train=100,
    test_size=50
):

    if len(values) < (
        minimum_train +
        test_size
    ):

        return {
            "available": False,
            "message":
                "Need more historical observations."
        }

    windows = []

    train_end = minimum_train

    while (
        train_end + test_size
        <= len(values)
    ):

        training = values[
            :train_end
        ]

        testing = values[
            train_end:
            train_end + test_size
        ]

        model_features = build_features(
            training
        )

        nexo_candidates = {
            x["panel"]
            for x in
            model_features[:10]
        }

        frequency_candidates = set(
            top_frequency(
                training,
                10
            )
        )

        recent_candidates = set(
            top_recent(
                training,
                10,
                50
            )
        )

        windows.append({

            "nexo":
                evaluate_candidates(
                    nexo_candidates,
                    testing
                ),

            "frequency":
                evaluate_candidates(
                    frequency_candidates,
                    testing
                ),

            "recent":
                evaluate_candidates(
                    recent_candidates,
                    testing
                ),

            "random":
                100 *
                (
                    10 /
                    max(
                        len(
                            set(values)
                        ),
                        1
                    )
                )

        })

        train_end += test_size

    if not windows:

        return {
            "available": False,
            "message":
                "No valid walk-forward windows."
        }

    return {

        "available": True,

        "windows":
            len(windows),

        "nexo":
            round(
                safe_mean(
                    [
                        x["nexo"]
                        for x in windows
                    ]
                ),
                3
            ),

        "frequency":
            round(
                safe_mean(
                    [
                        x["frequency"]
                        for x in windows
                    ]
                ),
                3
            ),

        "recent":
            round(
                safe_mean(
                    [
                        x["recent"]
                        for x in windows
                    ]
                ),
                3
            ),

        "random":
            round(
                safe_mean(
                    [
                        x["random"]
                        for x in windows
                    ]
                ),
                3
            )

    }


# ============================================================
# PERMUTATION TEST
# ============================================================

def permutation_test(
    values,
    repetitions=50
):

    if len(values) < 100:

        return {
            "available": False,
            "message":
                "Need at least 100 observations."
        }

    sample_size = min(
        len(values),
        500
    )

    original = values[
        -sample_size:
    ]

    # Keep the computation lightweight
    # for free hosting.

    original_features = build_features(
        original
    )

    original_score = (
        safe_mean(
            [
                x["score"]
                for x in
                original_features[:10]
            ]
        )
    )

    null_scores = []

    for _ in range(
        repetitions
    ):

        shuffled = list(
            original
        )

        random.shuffle(
            shuffled
        )

        features = build_features(
            shuffled
        )

        score = safe_mean(
            [
                x["score"]
                for x in
                features[:10]
            ]
        )

        null_scores.append(
            score
        )

    extreme = sum(
        score >= original_score
        for score in null_scores
    )

    p_value = (
        (extreme + 1) /
        (repetitions + 1)
    )

    return {

        "available": True,

        "repetitions":
            repetitions,

        "observed_top10_score":
            round(
                original_score,
                3
            ),

        "null_mean":
            round(
                safe_mean(
                    null_scores
                ),
                3
            ),

        "p_value":
            round(
                p_value,
                4
            )

    }


# ============================================================
# COMPLETE ANALYSIS
# ============================================================

def analyze(values):

    features = build_features(
        values
    )

    frequency = Counter(
        values
    )

    digits = Counter(
        "".join(values)
    )

    top_frequency_rows = [

        {
            "panel": panel,
            "count": count
        }

        for panel, count
        in frequency.most_common(25)

    ]

    digit_rows = [

        {
            "digit": str(i),
            "count":
                digits[str(i)]
        }

        for i in range(10)

    ]

    return {

        "records":
            len(values),

        "unique":
            len(frequency),

        "entropy":
            round(
                entropy(values),
                5
            ),

        "frequency":
            top_frequency_rows,

        "digits":
            digit_rows,

        "ranking":
            features[:20],

        "top10":
            features[:10],

        "walk_forward":
            walk_forward(values),

        "permutation":
            permutation_test(values)

    }


# ============================================================
# SYNC
# ============================================================

def sync_market(
    market
):

    if market not in MARKETS:
        raise ValueError(
            "Unknown market."
        )

    config = MARKETS[
        market
    ]

    html = fetch_source(
        config["url"]
    )

    source_hash = hashlib.sha256(
        html.encode(
            "utf-8",
            "ignore"
        )
    ).hexdigest()

    observations = extract_panels(
        html
    )

    if len(observations) < 10:
        raise RuntimeError(
            "Parser found too few panels. "
            "The source layout may have changed."
        )

    inserted = save_observations(
        market,
        source_hash,
        observations
    )

    values = load_values(
        market
    )

    analysis = analyze(
        values
    )

    conn = db()

    conn.execute(
        """
        INSERT INTO sync_log
        (
            market,
            source_hash,
            extracted,
            inserted,
            timestamp,
            status,
            error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market,
            source_hash,
            len(observations),
            inserted,
            datetime.now(
                timezone.utc
            ).isoformat(),
            "SUCCESS",
            None
        )
    )

    conn.commit()
    conn.close()

    return {

        "market":
            market,

        "name":
            config["name"],

        "source":
            config["url"],

        "extracted":
            len(observations),

        "inserted":
            inserted,

        "stored":
            len(values),

        "analysis":
            analysis

    }


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    return render_template(
        "index.html"
    )


@app.route(
    "/api/sync/<market>"
)
def api_sync(market):

    try:

        result = sync_market(
            market
        )

        return jsonify({
            "ok": True,
            **result
        })

    except Exception as error:

        return jsonify({

            "ok": False,

            "error":
                str(error)

        }), 500


@app.route(
    "/api/analysis/<market>"
)
def api_analysis(market):

    if market not in MARKETS:

        return jsonify({
            "ok": False,
            "error":
                "Unknown market"
        }), 404

    values = load_values(
        market
    )

    return jsonify({

        "ok": True,

        "market":
            market,

        "name":
            MARKETS[
                market
            ]["name"],

        "source":
            MARKETS[
                market
            ]["url"],

        "analysis":
            analyze(values)

    })


@app.route("/health")
def health():

    return jsonify({
        "status": "online",
        "engine": "NEXO v5",
        "mode": "historical research"
    })


init_db()


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
