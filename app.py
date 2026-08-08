from flask import Flask, jsonify, render_template, request
import requests
from bs4 import BeautifulSoup
from collections import Counter
import re

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    )
}

TIMEOUT = 25


MARKETS = {
    "kalyan-main-bazar": {
        "name": "Kalyan Main Bazar",
        "jodi": "https://dpbossss.boston/jodi-chart-record/kalyan-main-bazar.php",
        "panel": "https://dpbossss.boston/panel-chart-record/kalyan-main-bazar.php",
        "days": 7,
    },

    "main-bazar": {
        "name": "Main Bazar",
        "jodi": "https://dpbossss.boston/jodi-chart-record/main-bazar.php",
        "panel": "https://dpbossss.boston/panel-chart-record/main-bazar.php",
        "days": 5,
    }
}


def fetch_page(url):
    r = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT
    )

    r.raise_for_status()

    return r.text


def clean_cell(cell):
    return " ".join(
        cell.get_text(" ", strip=True).split()
    )


def get_tables(html):
    soup = BeautifulSoup(html, "html.parser")

    result = []

    for table in soup.find_all("table"):

        rows = []

        for tr in table.find_all("tr"):

            cells = [
                clean_cell(td)
                for td in tr.find_all(["th", "td"])
            ]

            if cells:
                rows.append(cells)

        if rows:
            result.append(rows)

    return result


def extract_jodis(html):
    """
    Jodi chart structure:

    Mon Tue Wed Thu Fri Sat Sun
    45  84  20  50  13  04  43

    For Main Bazar the number of weekdays can differ.
    """

    tables = get_tables(html)

    values = []
    records = []

    for table in tables:

        for row in table:

            # Ignore header rows.
            if not row:
                continue

            for cell in row:

                # Only exact 2-digit values.
                if re.fullmatch(r"\d{2}", cell):

                    values.append(cell)

    # Remove duplicates caused by menus/other tables only when necessary.
    # The historical table is retained in order.
    for value in values:
        records.append(value)

    return records


def extract_panels(html):
    """
    Panel chart structure:

    Date | Mon | Tue | ...

    Each day consists of:

    panel
    jodi
    panel
    jodi

    The panel itself is displayed as:

    3
    4
    7

    which must become:

    347
    """

    tables = get_tables(html)

    panels = []
    records = []

    for table in tables:

        for row in table:

            if len(row) < 2:
                continue

            # First column is normally the weekly date range.
            cells = row[1:]

            i = 0

            while i < len(cells):

                cell = cells[i].strip()

                # Panel cell:
                # "3 4 7"
                digits = re.findall(
                    r"\b\d\b",
                    cell
                )

                if len(digits) == 3:

                    panel = "".join(digits)

                    if re.fullmatch(
                        r"\d{3}",
                        panel
                    ):
                        panels.append(panel)

                i += 1

    for panel in panels:
        records.append(panel)

    return records


def frequency(values, limit=30):

    counter = Counter(values)

    return [
        {
            "value": value,
            "count": count
        }

        for value, count
        in counter.most_common(limit)
    ]


def digit_distribution(values):

    counter = Counter()

    for value in values:
        for digit in value:
            counter[digit] += 1

    return [
        {
            "digit": str(d),
            "count": counter[str(d)]
        }

        for d in range(10)
    ]


def gap_analysis(values, limit=30):

    last_seen = {}
    gaps = {}

    for index, value in enumerate(values):

        if value in last_seen:

            gaps[value] = (
                index -
                last_seen[value] -
                1
            )

        last_seen[value] = index

    result = sorted(
        gaps.items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]

    return [
        {
            "value": value,
            "gap": gap
        }

        for value, gap in result
    ]


def build_recent_table(jodi_values, panel_values):

    size = min(
        len(jodi_values),
        len(panel_values),
        30
    )

    result = []

    for i in range(size):

        result.append({
            "index": i + 1,
            "jodi": jodi_values[i],
            "panel": panel_values[i]
        })

    return result


@app.route("/")
def index():

    return render_template(
        "index.html"
    )


@app.route("/api/sync")
def sync():

    market_id = request.args.get(
        "market",
        "kalyan-main-bazar"
    )

    if market_id not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Invalid market"
        }), 400

    market = MARKETS[market_id]

    try:

        # -------------------------
        # JODI
        # -------------------------

        jodi_html = fetch_page(
            market["jodi"]
        )

        jodis = extract_jodis(
            jodi_html
        )


        # -------------------------
        # PANEL
        # -------------------------

        panel_html = fetch_page(
            market["panel"]
        )

        panels = extract_panels(
            panel_html
        )


        # -------------------------
        # RESULT
        # -------------------------

        result = {

            "ok": True,

            "market": market["name"],

            "sources": {
                "jodi": market["jodi"],
                "panel": market["panel"]
            },

            "records": {

                "jodi": len(jodis),

                "panel": len(panels),

                "total":
                    len(jodis) +
                    len(panels)
            },

            "jodi_frequency":
                frequency(
                    jodis
                ),

            "panel_frequency":
                frequency(
                    panels
                ),

            "jodi_digits":
                digit_distribution(
                    jodis
                ),

            "panel_digits":
                digit_distribution(
                    panels
                ),

            "jodi_gaps":
                gap_analysis(
                    jodis
                ),

            "panel_gaps":
                gap_analysis(
                    panels
                ),

            "recent":
                build_recent_table(
                    jodis,
                    panels
                )
        }


        return jsonify(result)


    except Exception as e:

        return jsonify({

            "ok": False,

            "error":
                str(e)

        }), 502


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
