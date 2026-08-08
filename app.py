from flask import Flask, jsonify, request, render_template
import requests
import re
from collections import Counter

app = Flask(__name__)

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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/sync")
def sync():

    market_key = request.args.get("market", "kalyan")

    if market_key not in MARKETS:
        return jsonify({
            "ok": False,
            "error": "Invalid market"
        }), 400

    market = MARKETS[market_key]

    try:

        response = requests.get(
            market["url"],
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            }
        )

        response.raise_for_status()

        html = response.text

        # Extract 3-digit numeric historical values
        tokens = re.findall(
            r"(?<!\d)\d{3}(?!\d)",
            html
        )

        # Remove duplicates while preserving order
        values = list(dict.fromkeys(tokens))

        digit_counter = Counter()

        for value in tokens:

            for digit in value:
                digit_counter[digit] += 1


        panel_counter = Counter(tokens)


        top_panels = [
            {
                "value": value,
                "count": count
            }

            for value, count
            in panel_counter.most_common(30)
        ]


        digit_frequency = [
            {
                "digit": digit,
                "count": digit_counter[digit]
            }

            for digit in "0123456789"
        ]


        return jsonify({

            "ok": True,

            "market":
                market["name"],

            "source":
                market["url"],

            "rows_found":
                len(values),

            "analysis": {

                "records":
                    len(tokens),

                "digit_frequency":
                    digit_frequency,

                "top_panels":
                    top_panels

            }

        })


    except Exception as e:

        return jsonify({

            "ok": False,

            "error":
                str(e)

        }), 500


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=10000
    )
