from flask import Flask, jsonify, request, render_template
import requests
import re
from collections import Counter
from bs4 import BeautifulSoup
from datetime import datetime

app = Flask(__name__)


# ============================================================
# MARKET CONFIGURATION
# ============================================================

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


# ============================================================
# REQUEST HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "NEXO Historical Analytics",
        "status": "online"
    })


# ============================================================
# DATE PARSER
# ============================================================

def normalize_date(text):

    if not text:
        return None

    text = str(text).strip()

    formats = [
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y-%m-%d",
        "%d-%m-%y",
        "%d/%m/%y",
        "%B %d, %Y",
        "%b %d, %Y"
    ]

    for fmt in formats:

        try:

            dt = datetime.strptime(text, fmt)

            return dt.strftime("%Y-%m-%d")

        except ValueError:
            pass

    return None


# ============================================================
# EXTRACT 3 DIGIT VALUES
# ============================================================

def extract_three_digit_values(text):

    if not text:
        return []

    values = re.findall(
        r"(?<!\d)\d{3}(?!\d)",
        text
    )

    return values


# ============================================================
# EXTRACT DATE-LIKE VALUES
# ============================================================

def extract_dates(text):

    if not text:
        return []

    patterns = [

        r"\b\d{2}-\d{2}-\d{4}\b",

        r"\b\d{2}/\d{2}/\d{4}\b",

        r"\b\d{4}-\d{2}-\d{2}\b",

        r"\b\d{2}-\d{2}-\d{2}\b",

        r"\b\d{2}/\d{2}/\d{2}\b"

    ]

    found = []

    for pattern in patterns:

        found.extend(
            re.findall(pattern, text)
        )

    return found


# ============================================================
# ANALYZE VALUES
# ============================================================

def analyze_values(values):

    digit_counter = Counter()

    panel_counter = Counter()

    for value in values:

        panel_counter[value] += 1

        for digit in value:

            digit_counter[digit] += 1


    top_panels = []

    for value, count in panel_counter.most_common(50):

        top_panels.append({
            "value": value,
            "count": count
        })


    digit_frequency = []

    for digit in "0123456789":

        digit_frequency.append({
            "digit": digit,
            "count": digit_counter[digit]
        })


    return {

        "records": len(values),

        "unique_values": len(panel_counter),

        "digit_frequency": digit_frequency,

        "top_panels": top_panels

    }


# ============================================================
# TABLE ANALYSIS
# ============================================================

def extract_table_records(html, selected_date=None):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    records = []

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

        for row in rows:

            cells = row.find_all(
                ["td", "th"]
            )

            if not cells:
                continue

            row_text = " ".join(
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            )

            row_date = None

            dates = extract_dates(row_text)

            for date_text in dates:

                normalized = normalize_date(
                    date_text
                )

                if normalized:

                    row_date = normalized

                    break


            # ------------------------------------------------
            # If a date is selected and the row has a date,
            # only keep matching rows.
            # ------------------------------------------------

            if selected_date and row_date:

                if row_date != selected_date:
                    continue


            values = extract_three_digit_values(
                row_text
            )

            records.extend(values)


    return records


# ============================================================
# MAIN SYNC API
# ============================================================

@app.route("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan"
    ).strip().lower()


    selected_date = request.args.get(
        "date",
        ""
    ).strip()


    # --------------------------------------------------------
    # Validate market
    # --------------------------------------------------------

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Invalid market selected."
        }), 400


    market = MARKETS[market_key]


    # --------------------------------------------------------
    # Validate date
    # --------------------------------------------------------

    if selected_date:

        try:

            datetime.strptime(
                selected_date,
                "%Y-%m-%d"
            )

        except ValueError:

            return jsonify({
                "ok": False,
                "error": "Invalid date format."
            }), 400


    # --------------------------------------------------------
    # Fetch source
    # --------------------------------------------------------

    try:

        response = requests.get(
            market["url"],
            headers=HEADERS,
            timeout=25
        )

        response.raise_for_status()

        html = response.text


    except requests.RequestException as error:

        return jsonify({

            "ok": False,

            "error":
                f"Source request failed: {error}"

        }), 502


    # --------------------------------------------------------
    # Parse historical records
    # --------------------------------------------------------

    records = extract_table_records(
        html,
        selected_date
    )


    # --------------------------------------------------------
    # Fallback extraction
    # --------------------------------------------------------

    if not records:

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        text = soup.get_text(
            " ",
            strip=True
        )

        records = extract_three_digit_values(
            text
        )


    # --------------------------------------------------------
    # Analysis
    # --------------------------------------------------------

    analysis = analyze_values(
        records
    )


    # --------------------------------------------------------
    # Source date information
    # --------------------------------------------------------

    source_dates = []

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    source_text = soup.get_text(
        " ",
        strip=True
    )

    for date_text in extract_dates(
        source_text
    ):

        normalized = normalize_date(
            date_text
        )

        if normalized:

            source_dates.append(
                normalized
            )


    source_dates = sorted(
        set(source_dates)
    )


    # --------------------------------------------------------
    # Response
    # --------------------------------------------------------

    return jsonify({

        "ok": True,

        "market": market["name"],

        "market_key": market_key,

        "date": selected_date or None,

        "source": market["url"],

        "rows_found": len(records),

        "available_dates": source_dates[-100:],

        "analysis": analysis

    })


# ============================================================
# RUN LOCAL
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
