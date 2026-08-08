from __future__ import annotations

from collections import Counter
from statistics import mean


def frequency(values):

    counter = Counter(values)

    return [
        {
            "value": value,
            "count": count
        }
        for value, count
        in counter.most_common()
    ]


def digit_distribution(values):

    counter = Counter()

    for value in values:
        counter.update(value)

    return [
        {
            "digit": digit,
            "count": counter[digit]
        }
        for digit in "0123456789"
    ]


def position_distribution(values):

    counters = [
        Counter(),
        Counter(),
        Counter()
    ]

    for value in values:

        if len(value) != 3:
            continue

        for position, digit in enumerate(value):
            counters[position][digit] += 1

    output = []

    for index, counter in enumerate(counters):

        output.append({

            "position":
                index + 1,

            "distribution": [
                {
                    "digit": digit,
                    "count": counter[digit]
                }
                for digit in "0123456789"
            ]

        })

    return output


def gap_analysis(values):

    positions = {}

    for index, value in enumerate(values):

        positions.setdefault(
            value,
            []
        ).append(index)

    result = []

    total = len(values)

    for value, indexes in positions.items():

        gaps = [
            indexes[i] - indexes[i - 1]
            for i in range(
                1,
                len(indexes)
            )
        ]

        last_seen = (
            total - 1 - indexes[-1]
        )

        result.append({

            "value":
                value,

            "occurrences":
                len(indexes),

            "last_seen":
                last_seen,

            "average_gap":
                round(
                    mean(gaps),
                    2
                )
                if gaps
                else None,

            "min_gap":
                min(gaps)
                if gaps
                else None,

            "max_gap":
                max(gaps)
                if gaps
                else None

        })

    result.sort(
        key=lambda item: (
            -item["occurrences"],
            item["last_seen"]
        )
    )

    return result


def recent_analysis(values):

    if not values:
        return []

    window = min(
        100,
        len(values)
    )

    recent = values[-window:]

    full = Counter(values)
    recent_counter = Counter(recent)

    result = []

    for value, total in full.items():

        recent_count = (
            recent_counter[value]
        )

        historical_rate = (
            total / len(values)
        )

        recent_rate = (
            recent_count / len(recent)
        )

        result.append({

            "value":
                value,

            "total":
                total,

            "recent":
                recent_count,

            "change":
                round(
                    recent_rate -
                    historical_rate,
                    6
                )

        })

    result.sort(
        key=lambda item:
            item["change"],
        reverse=True
    )

    return result


def candidate_ranking(values):

    if len(values) < 20:
        return []

    total = len(values)

    recent_size = min(
        100,
        total
    )

    recent = values[-recent_size:]

    full_counter = Counter(values)
    recent_counter = Counter(recent)

    max_frequency = max(
        full_counter.values()
    )

    max_recent = max(
        recent_counter.values(),
        default=1
    )

    gaps = gap_analysis(values)

    gap_map = {
        item["value"]: item
        for item in gaps
    }

    position_counters = [
        Counter(),
        Counter(),
        Counter()
    ]

    for value in values:

        if len(value) != 3:
            continue

        for position, digit in enumerate(value):
            position_counters[
                position
            ][digit] += 1

    candidates = []

    for value, count in full_counter.items():

        frequency_score = (
            count /
            max_frequency
        ) * 40

        recent_score = (
            recent_counter[value] /
            max_recent
        ) * 30

        position_score = 0

        if len(value) == 3:

            for position, digit in enumerate(value):

                position_total = sum(
                    position_counters[
                        position
                    ].values()
                )

                if position_total:

                    position_score += (
                        position_counters[
                            position
                        ][digit] /
                        position_total
                    ) * 10

        gap_score = 0

        gap = gap_map.get(value)

        if gap:

            average_gap = (
                gap["average_gap"]
            )

            if (
                average_gap is not None
                and average_gap > 0
            ):

                ratio = (
                    gap["last_seen"] /
                    average_gap
                )

                gap_score = min(
                    ratio,
                    2
                ) / 2 * 20

        score = min(
            frequency_score +
            recent_score +
            position_score +
            gap_score,
            100
        )

        candidates.append({

            "value":
                value,

            "score":
                round(score, 2),

            "frequency":
                count,

            "recent":
                recent_counter[value],

            "last_seen":
                gap["last_seen"]
                if gap
                else None,

            "average_gap":
                gap["average_gap"]
                if gap
                else None

        })

    candidates.sort(
        key=lambda item:
            item["score"],
        reverse=True
    )

    return candidates[:20]


def backtest(values):

    if len(values) < 100:

        return {

            "available":
                False,

            "tests":
                0,

            "hits":
                0,

            "hit_rate":
                None,

            "message":
                "Need at least 100 records."

        }

    tests = 0
    hits = 0

    for index in range(
        50,
        len(values)
    ):

        history = values[:index]

        top = {
            value
            for value, _ in
            Counter(history)
            .most_common(10)
        }

        actual = values[index]

        tests += 1

        if actual in top:
            hits += 1

    rate = (
        hits / tests * 100
    )

    return {

        "available":
            True,

        "tests":
            tests,

        "hits":
            hits,

        "hit_rate":
            round(rate, 2),

        "message":
            "Historical walk-forward evaluation."

    }


def analyze(values):

    gaps = gap_analysis(values)

    return {

        "records":
            len(values),

        "unique":
            len(set(values)),

        "frequency":
            frequency(values)[:50],

        "top_panels":
            frequency(values)[:20],

        "digits":
            digit_distribution(values),

        "positions":
            position_distribution(values),

        "gaps":
            gaps[:100],

        "recent":
            recent_analysis(values)[:50],

        "candidates":
            candidate_ranking(values),

        "backtest":
            backtest(values)

    }
