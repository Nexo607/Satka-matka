from __future__ import annotations

from datetime import datetime, timezone

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request
)

import database
from analytics import analyze


app = Flask(__name__)


MARKETS = {

    "kalyan": {
        "name": "Kalyan",
        "url":
            "https://dpbossss.boston/panel-chart-record/kalyan.php"
    },

    "main-bazar": {
        "name": "Main Bazar",
        "url":
            "https://dpbossss.boston/panel-chart-record/main-bazar.php"
    }

}


HEADERS = {

    "User-Agent":
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"

}


def now():

    return datetime.now(
        timezone.utc
    ).isoformat()


def extract_values(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    values = []

    for cell in soup.find_all(
        ["td", "th"]
    ):

        text = cell.get_text(
            " ",
            strip=True
        )

        import re

        values.extend(
            re.findall(
                r"(?<!\d)\d{3}(?!\d)",
                text
            )
        )

    if not values:

        import re

        text = soup.get_text(
            " ",
            strip=True
        )

        values = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            text
        )

    return values


def sync_market(
    market_key
):

    if market_key not in MARKETS:

        raise ValueError(
            "Unknown market."
        )

    market = MARKETS[
        market_key
    ]

    started = now()

    try:

        response = requests.get(
            market["url"],
            headers=HEADERS,
            timeout=30
        )

        response.raise_for_status()

        values = extract_values(
            response.text
        )

        if not values:

            raise RuntimeError(
                "No historical 3-digit values detected."
            )

        added = database.insert_results(
            market_key,
            values,
            market["url"],
            now()
        )

        database.save_sync_run(
            market_key,
            started,
            now(),
            "success",
            len(values),
            added
        )

        return {

            "ok":
                True,

            "market":
                market["name"],

            "records_found":
                len(values),

            "records_added":
                added,

            "http_status":
                response.status_code,

            "html_size":
                len(response.text)

        }

    except Exception as exc:

        database.save_sync_run(
            market_key,
            started,
            now(),
            "error",
            0,
            0,
            str(exc)
        )

        raise


def automatic_sync():

    for market_key in MARKETS:

        try:

            result = sync_market(
                market_key
            )

            app.logger.info(
                "AUTO SYNC %s: %s",
                market_key,
                result
            )

        except Exception as exc:

            app.logger.error(
                "AUTO SYNC ERROR %s: %s",
                market_key,
                exc
            )


@app.get("/")
def index():

    return render_template(
        "index.html"
    )


@app.get("/health")
def health():

    return jsonify({

        "ok":
            True,

        "service":
            "NEXO Analytics v3",

        "database":
            "sqlite",

        "status":
            "online"

    })


@app.get("/api/markets")
def markets():

    return jsonify({

        "ok":
            True,

        "markets":
            MARKETS

    })


@app.get("/api/sync")
def sync():

    market_key = request.args.get(
        "market",
        "kalyan"
    ).strip().lower()

    try:

        result = sync_market(
            market_key
        )

        values = database.get_values(
            market_key
        )

        return jsonify({

            "ok":
                True,

            "sync":
                result,

            "analysis":
                analyze(values)

        })

    except requests.Timeout:

        return jsonify({

            "ok":
                False,

            "error":
                "Source timed out."

        }), 504

    except requests.HTTPError as exc:

        return jsonify({

            "ok":
                False,

            "error":
                f"Source HTTP error: {exc}"

        }), 502

    except ValueError as exc:

        return jsonify({

            "ok":
                False,

            "error":
                str(exc)

        }), 400

    except Exception as exc:

        app.logger.exception(
            "Sync failure"
        )

        return jsonify({

            "ok":
                False,

            "error":
                str(exc)

        }), 500


@app.get("/api/analysis")
def analysis():

    market_key = request.args.get(
        "market",
        "kalyan"
    ).strip().lower()

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Unknown market."
        }), 400

    values = database.get_values(
        market_key
    )

    return jsonify({

        "ok":
            True,

        "market":
            MARKETS[market_key],

        "analysis":
            analyze(values),

        "latest_sync":
            database.latest_sync(
                market_key
            )

    })


@app.get("/api/export/json")
def export_json():

    market_key = request.args.get(
        "market",
        "kalyan"
    )

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Unknown market."
        }), 400

    content = database.export_json(
        market_key
    )

    return Response(
        content,
        mimetype="application/json",
        headers={
            "Content-Disposition":
                f"attachment; filename={market_key}.json"
        }
    )


@app.get("/api/export/csv")
def export_csv():

    market_key = request.args.get(
        "market",
        "kalyan"
    )

    if market_key not in MARKETS:

        return jsonify({
            "ok": False,
            "error": "Unknown market."
        }), 400

    content = database.export_csv(
        market_key
    )

    return Response(
        content,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f"attachment; filename={market_key}.csv"
        }
    )


# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

database.init_db()

scheduler = BackgroundScheduler()

scheduler.add_job(
    automatic_sync,
    "interval",
    minutes=30,
    id="nexo_auto_sync",
    replace_existing=True
)

scheduler.start()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False
    )
