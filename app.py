from __future__ import annotations

from datetime import datetime
from flask import Flask, jsonify, request, send_file
import io
import json

import database
import analytics


# ============================================================
# NEXO // HISTORICAL ANALYTICS v6
# Flask API
# ============================================================

app = Flask(__name__)

# Initialize / migrate SQLite database on startup.
database.init_db()


# ============================================================
# CONFIGURATION
# ============================================================

MARKET_TIME_SLOTS = {
    "Kalyan": [
        "16:02",
        "18:02",
    ],
    "Main Bazar": [],
}


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_market():
    return (
        request.args.get("market")
        or request.form.get("market")
        or "Kalyan"
    ).strip()


def get_records_for_market(market):
    return database.get_result_rows(market)


def json_error(message, status=400):
    return jsonify({
        "ok": False,
        "error": message
    }), status


# ============================================================
# HEALTH
# ============================================================

@app.route("/")
def index():
    return jsonify({
        "name": "NEXO // HISTORICAL ANALYTICS",
        "version": "v6",
        "status": "online",
        "features": [
            "historical analytics",
            "time-slot analysis",
            "intraday sequence tracking",
            "frequency",
            "gap analysis",
            "recency",
            "momentum",
            "repetition",
            "digit-position distribution",
            "walk-forward backtesting"
        ]
    })


@app.route("/health")
@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "status": "online",
        "version": "v6",
        "database": "sqlite"
    })


# ============================================================
# MARKET CONFIGURATION
# ============================================================

@app.route("/api/markets", methods=["GET"])
def markets():

    try:
        with database.get_connection() as db:

            rows = db.execute("""
                SELECT DISTINCT market
                FROM results
                WHERE market IS NOT NULL
                ORDER BY market
            """).fetchall()

        markets_from_db = [
            row["market"]
            for row in rows
        ]

        markets_from_config = list(
            MARKET_TIME_SLOTS.keys()
        )

        markets = sorted(
            set(
                markets_from_db +
                markets_from_config
            )
        )

        return jsonify({
            "ok": True,
            "markets": markets
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# TIME-SLOT CONFIGURATION
# ============================================================

@app.route("/api/time-slots", methods=["GET"])
def get_time_slots():

    market = get_market()

    try:

        configured = database.get_market_time_slots(
            market
        )

        configured_times = [
            row["update_time"]
            for row in configured
            if row["enabled"]
        ]

        # Built-in defaults are used only when no database
        # configuration exists.
        if not configured_times:

            configured_times = MARKET_TIME_SLOTS.get(
                market,
                []
            )

        observed = database.get_available_time_slots(
            market
        )

        all_slots = sorted(
            set(
                configured_times +
                observed
            )
        )

        return jsonify({
            "ok": True,
            "market": market,
            "slots": all_slots,
            "configured": configured_times,
            "observed": observed
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


@app.route("/api/time-slots", methods=["POST"])
def add_time_slot():

    data = request.get_json(
        silent=True
    ) or {}

    market = (
        data.get("market")
        or get_market()
    ).strip()

    update_time = (
        data.get("update_time")
        or ""
    ).strip()

    if not market:
        return json_error(
            "market is required"
        )

    if not update_time:
        return json_error(
            "update_time is required"
        )

    try:

        database.save_market_time_slot(
            market,
            update_time,
            True
        )

        return jsonify({
            "ok": True,
            "market": market,
            "update_time": update_time
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


@app.route("/api/time-slots", methods=["DELETE"])
def delete_time_slot():

    market = get_market()

    update_time = (
        request.args.get(
            "update_time"
        )
        or ""
    ).strip()

    if not update_time:
        return json_error(
            "update_time is required"
        )

    try:

        database.delete_market_time_slot(
            market,
            update_time
        )

        return jsonify({
            "ok": True
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# ADD HISTORICAL / INTRADAY RESULT
# ============================================================

@app.route("/api/results", methods=["POST"])
@app.route("/api/records", methods=["POST"])
def add_result():

    data = request.get_json(
        silent=True
    )

    if not data:
        data = request.form.to_dict()

    market = (
        data.get("market")
        or ""
    ).strip()

    value = (
        data.get("value")
        or data.get("result")
        or ""
    ).strip()

    result_date = (
        data.get("result_date")
        or data.get("date")
        or ""
    ).strip()

    update_time = (
        data.get("update_time")
        or data.get("time")
        or ""
    ).strip()

    note = (
        data.get("note")
        or None
    )

    try:
        sequence = int(
            data.get(
                "sequence",
                1
            )
        )
    except (
        TypeError,
        ValueError
    ):
        sequence = 1

    if not market:
        return json_error(
            "market is required"
        )

    if not value:
        return json_error(
            "value/result is required"
        )

    if not result_date:

        result_date = datetime.utcnow().strftime(
            "%Y-%m-%d"
        )

    if not update_time:

        update_time = datetime.utcnow().strftime(
            "%H:%M"
        )

    try:

        added = database.insert_time_slot_result(
            market=market,
            value=value,
            result_date=result_date,
            update_time=update_time,
            sequence=sequence,
            note=note,
            fetched_at=now_iso()
        )

        if added == 0:

            return jsonify({
                "ok": False,
                "duplicate": True,
                "message":
                    "This market/date/time/sequence/result "
                    "already exists."
            }), 409

        return jsonify({
            "ok": True,
            "added": 1,
            "record": {
                "market": market,
                "value": value,
                "result_date": result_date,
                "update_time": update_time,
                "sequence": sequence,
                "note": note
            }
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# HISTORICAL RECORDS
# ============================================================

@app.route("/api/results", methods=["GET"])
@app.route("/api/records", methods=["GET"])
def get_results():

    market = get_market()

    try:

        rows = database.get_result_rows(
            market
        )

        limit_raw = request.args.get(
            "limit"
        )

        if limit_raw:

            try:
                limit = int(
                    limit_raw
                )

                rows = rows[
                    -limit:
                ]

            except ValueError:
                pass

        return jsonify({
            "ok": True,
            "market": market,
            "count": len(rows),
            "records": rows
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


@app.route("/api/results/date", methods=["GET"])
def get_date_results():

    market = get_market()

    result_date = (
        request.args.get(
            "date"
        )
        or ""
    ).strip()

    if not result_date:

        return json_error(
            "date is required"
        )

    try:

        rows = database.get_date_rows(
            market,
            result_date
        )

        return jsonify({
            "ok": True,
            "market": market,
            "date": result_date,
            "count": len(rows),
            "records": rows
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# TIME-SLOT HISTORY
# ============================================================

@app.route("/api/results/time-slot", methods=["GET"])
def get_slot_results():

    market = get_market()

    update_time = (
        request.args.get(
            "update_time"
        )
        or ""
    ).strip()

    if not update_time:

        return json_error(
            "update_time is required"
        )

    limit = request.args.get(
        "limit"
    )

    try:

        limit_value = (
            int(limit)
            if limit
            else None
        )

    except ValueError:

        limit_value = None

    try:

        rows = database.get_time_slot_rows(
            market,
            update_time,
            limit_value
        )

        return jsonify({
            "ok": True,
            "market": market,
            "update_time": update_time,
            "count": len(rows),
            "records": rows
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# V6 ANALYTICS
# ============================================================

@app.route("/api/analytics", methods=["GET"])
def market_analytics():

    market = get_market()

    try:

        records = get_records_for_market(
            market
        )

        window_raw = request.args.get(
            "window",
            "100"
        )

        try:
            window = int(
                window_raw
            )
        except ValueError:
            window = 100

        if window > 0:
            records = records[
                -window:
            ]

        result = analytics.analyze_market(
            records
        )

        result.update({
            "ok": True,
            "market": market,
            "window": window
        })

        return jsonify(
            result
        )

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# V6 TIME-SLOT ANALYTICS
# ============================================================

@app.route("/api/analytics/time-slots", methods=["GET"])
def analytics_time_slots():

    market = get_market()

    try:

        records = get_records_for_market(
            market
        )

        window_raw = request.args.get(
            "window",
            "100"
        )

        try:
            window = int(
                window_raw
            )
        except ValueError:
            window = 100

        if window > 0:

            records = records[
                -window:
            ]

        result = analytics.analyze_all_time_slots(
            records
        )

        return jsonify({
            "ok": True,
            "market": market,
            "window": window,
            "slots": result,
            "disclaimer":
                "Time-slot statistics are historical "
                "experimental analysis only."
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


@app.route("/api/analytics/time-slot", methods=["GET"])
def analytics_single_time_slot():

    market = get_market()

    update_time = (
        request.args.get(
            "update_time"
        )
        or ""
    ).strip()

    if not update_time:

        return json_error(
            "update_time is required"
        )

    try:

        records = get_records_for_market(
            market
        )

        result = analytics.build_time_slot_ranking(
            records,
            update_time
        )

        return jsonify({
            "ok": True,
            "market": market,
            **result,
            "disclaimer":
                "Time-slot ranking is historical "
                "experimental analysis only."
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# WALK-FORWARD BACKTEST
# ============================================================

@app.route("/api/backtest", methods=["GET"])
def backtest():

    market = get_market()

    try:

        min_history = int(
            request.args.get(
                "min_history",
                "10"
            )
        )

    except ValueError:

        min_history = 10

    try:

        records = get_records_for_market(
            market
        )

        result = analytics.walk_forward_backtest(
            records,
            min_history=min_history
        )

        return jsonify({
            "ok": True,
            "market": market,
            **result
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# TIME-SLOT WALK-FORWARD BACKTEST
# ============================================================

@app.route(
    "/api/backtest/time-slot",
    methods=["GET"]
)
def time_slot_backtest():

    market = get_market()

    update_time = (
        request.args.get(
            "update_time"
        )
        or ""
    ).strip()

    if not update_time:

        return json_error(
            "update_time is required"
        )

    try:

        min_history = int(
            request.args.get(
                "min_history",
                "10"
            )
        )

    except ValueError:

        min_history = 10

    try:

        records = get_records_for_market(
            market
        )

        result = analytics.slot_walk_forward_backtest(
            records,
            update_time,
            min_history=min_history
        )

        return jsonify({
            "ok": True,
            "market": market,
            "update_time": update_time,
            **result
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# REFERENCE DATA
# ============================================================

@app.route(
    "/api/seed-reference",
    methods=["POST"]
)
def seed_reference():

    examples = [

        {
            "market": "Kalyan",
            "value": "459-81-227",
            "result_date": "2021-08-11",
            "update_time": "16:02",
            "sequence": 1,
            "note":
                "Reference image example. "
                "Handwritten annotations: 18 and 11."
        },

        {
            "market": "Main Bazar",
            "value": "467-78-260",
            "result_date": "2020-08-10",
            "update_time": "00:00",
            "sequence": 1,
            "note":
                "Reference image example. "
                "Handwritten annotations: 17 and 8."
        }

    ]

    added = 0

    try:

        for item in examples:

            added += database.insert_time_slot_result(
                market=item["market"],
                value=item["value"],
                result_date=item["result_date"],
                update_time=item["update_time"],
                sequence=item["sequence"],
                note=item["note"],
                fetched_at=now_iso()
            )

        return jsonify({
            "ok": True,
            "added": added,
            "message":
                "Reference examples loaded."
        })

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# EXPORT
# ============================================================

@app.route(
    "/api/export/json",
    methods=["GET"]
)
def export_json():

    market = get_market()

    try:

        content = database.export_json(
            market
        )

        response = app.response_class(
            content,
            mimetype="application/json"
        )

        response.headers[
            "Content-Disposition"
        ] = (
            f"attachment; "
            f"filename={market}_nexo_v6.json"
        )

        return response

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


@app.route(
    "/api/export/csv",
    methods=["GET"]
)
def export_csv():

    market = get_market()

    try:

        content = database.export_csv(
            market
        )

        response = app.response_class(
            content,
            mimetype="text/csv"
        )

        response.headers[
            "Content-Disposition"
        ] = (
            f"attachment; "
            f"filename={market}_nexo_v6.csv"
        )

        return response

    except Exception as exc:

        return json_error(
            str(exc),
            500
        )


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({
        "ok": False,
        "error": "Endpoint not found",
        "version": "v6"
    }), 404


@app.errorhandler(500)
def server_error(error):

    return jsonify({
        "ok": False,
        "error": "Internal server error",
        "version": "v6"
    }), 500


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
