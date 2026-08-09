import math
from collections import Counter


def normalize(value, minimum, maximum):
    if maximum <= minimum:
        return 0.5
    return (value - minimum) / (maximum - minimum)


def analyze(records):
    panels = [r["panel"] for r in records if valid_panel(r["panel"])]

    if not panels:
        return {
            "records": 0,
            "unique": 0,
            "frequency": [],
            "digit_frequency": [],
            "gaps": [],
            "top_historical": []
        }

    frequency = Counter(panels)

    digit_frequency = Counter()

    for panel in panels:
        for digit in panel:
            digit_frequency[digit] += 1

    # Last occurrence position.
    last_seen = {}

    for index, panel in enumerate(panels):
        last_seen[panel] = index

    total = len(panels)

    candidates = []

    for panel, count in frequency.items():

        last_index = last_seen[panel]
        gap = total - 1 - last_index

        # Frequency component.
        freq_score = count / total

        # Recency component.
        recency_score = 1 / (1 + gap)

        # Moderate gap component.
        gap_score = 1 / math.sqrt(1 + gap)

        # Digit diversity.
        digits = [int(x) for x in panel]
        digit_mean = sum(digits) / 3
        digit_variance = sum(
            (x - digit_mean) ** 2 for x in digits
        ) / 3

        digit_stability = 1 / (1 + digit_variance)

        # Composite HISTORICAL score.
        score = (
            0.40 * freq_score +
            0.30 * recency_score +
            0.20 * gap_score +
            0.10 * digit_stability
        )

        candidates.append({
            "panel": panel,
            "occurrences": count,
            "gap": gap,
            "frequency_score": round(freq_score, 6),
            "recency_score": round(recency_score, 6),
            "gap_score": round(gap_score, 6),
            "digit_stability": round(digit_stability, 6),
            "score": round(score, 6)
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return {
        "records": total,
        "unique": len(frequency),
        "frequency": [
            {
                "panel": panel,
                "count": count
            }
            for panel, count in frequency.most_common(50)
        ],
        "digit_frequency": [
            {
                "digit": digit,
                "count": digit_frequency[digit]
            }
            for digit in sorted(digit_frequency)
        ],
        "gaps": sorted(
            [
                {
                    "panel": panel,
                    "gap": total - 1 - last_seen[panel]
                }
                for panel in frequency
            ],
            key=lambda x: x["gap"]
        )[:50],
        "top_historical": candidates[:10]
    }


def valid_panel(value):
    if value is None:
        return False

    value = str(value).strip()

    if len(value) != 3:
        return False

    return value.isdigit()
