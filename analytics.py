from __future__ import annotations

from collections import Counter, defaultdict
from math import sqrt
from typing import Any


# ============================================================
# NEXO // HISTORICAL ANALYTICS v6
# ============================================================
#
# V6 additions:
#   - time-slot analysis
#   - intraday sequence analysis
#   - frequency
#   - gap analysis
#   - recency weighting
#   - momentum
#   - repetition
#   - digit-position distribution
#   - stability / variance
#   - composite ranking
#   - walk-forward backtesting
#
# Results are statistical/experimental scores only.
# ============================================================


def _safe_int(value: Any):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_value(value: Any) -> list[int]:
    """
    Extract numeric components from values such as:

        459-81-227
        459 81 227
        459/81/227

    Returns:
        [459, 81, 227]
    """

    if value is None:
        return []

    text = str(value).strip()

    for separator in ["/", "_", " ", ","]:
        text = text.replace(separator, "-")

    parts = []

    for token in text.split("-"):

        token = token.strip()

        if token.isdigit():
            parts.append(int(token))

    return parts


def flatten_values(records: list[dict]) -> list[int]:

    values = []

    for record in records:

        value = record.get("value")

        values.extend(
            parse_value(value)
        )

    return values


def frequency_analysis(
    records: list[dict]
) -> dict[int, int]:

    values = flatten_values(records)

    return dict(
        Counter(values)
    )


def recent_frequency_analysis(
    records: list[dict],
    recent_window: int = 20
) -> dict[int, int]:

    recent_records = records[
        -recent_window:
    ]

    values = flatten_values(
        recent_records
    )

    return dict(
        Counter(values)
    )


def gap_analysis(
    records: list[dict]
) -> dict[int, int | None]:

    values = flatten_values(records)

    last_seen: dict[int, int] = {}
    gaps: dict[int, int | None] = {}

    for index, value in enumerate(values):

        if value in last_seen:

            gaps[value] = (
                index -
                last_seen[value]
            )

        else:

            gaps[value] = None

        last_seen[value] = index

    return gaps


def recency_scores(
    records: list[dict],
    decay: float = 0.94
) -> dict[int, float]:

    values = flatten_values(records)

    scores: defaultdict[int, float] = defaultdict(float)

    if not values:
        return {}

    total = len(values)

    for index, value in enumerate(values):

        distance = (
            total -
            index -
            1
        )

        weight = decay ** distance

        scores[value] += weight

    maximum = max(
        scores.values(),
        default=1.0
    )

    return {
        value: score / maximum
        for value, score in scores.items()
    }


def momentum_analysis(
    records: list[dict],
    recent_window: int = 20
) -> dict[int, float]:

    all_frequency = frequency_analysis(
        records
    )

    recent_frequency = recent_frequency_analysis(
        records,
        recent_window
    )

    total = max(
        1,
        sum(all_frequency.values())
    )

    recent_total = max(
        1,
        sum(recent_frequency.values())
    )

    values = set(
        all_frequency
    ).union(
        recent_frequency
    )

    momentum = {}

    for value in values:

        long_ratio = (
            all_frequency.get(
                value,
                0
            ) /
            total
        )

        recent_ratio = (
            recent_frequency.get(
                value,
                0
            ) /
            recent_total
        )

        momentum[value] = (
            recent_ratio -
            long_ratio
        )

    return momentum


def repetition_analysis(
    records: list[dict]
) -> dict[int, int]:

    values = flatten_values(
        records
    )

    return dict(
        Counter(values)
    )


def digit_position_distribution(
    records: list[dict]
) -> dict[str, dict[int, int]]:

    positions: defaultdict[
        str,
        Counter
    ] = defaultdict(Counter)

    for record in records:

        parts = parse_value(
            record.get("value")
        )

        for position, value in enumerate(
            parts,
            start=1
        ):

            positions[
                str(position)
            ][value] += 1

    return {
        position: dict(counter)
        for position, counter in positions.items()
    }


def calculate_variance(
    values: list[float]
) -> float:

    if len(values) < 2:
        return 0.0

    mean = (
        sum(values) /
        len(values)
    )

    return (
        sum(
            (value - mean) ** 2
            for value in values
        )
        /
        len(values)
    )


def stability_analysis(
    records: list[dict]
) -> dict[str, float]:

    values = flatten_values(
        records
    )

    if not values:

        return {
            "mean": 0.0,
            "variance": 0.0,
            "standard_deviation": 0.0,
            "stability": 0.0
        }

    mean = (
        sum(values) /
        len(values)
    )

    variance = calculate_variance(
        values
    )

    standard_deviation = sqrt(
        variance
    )

    stability = (
        1.0 /
        (1.0 + standard_deviation)
    )

    return {
        "mean": round(
            mean,
            6
        ),
        "variance": round(
            variance,
            6
        ),
        "standard_deviation": round(
            standard_deviation,
            6
        ),
        "stability": round(
            stability,
            6
        )
    }


def time_slot_records(
    records: list[dict]
) -> dict[str, list[dict]]:

    grouped: defaultdict[
        str,
        list
    ] = defaultdict(list)

    for record in records:

        slot = (
            record.get(
                "update_time"
            )
            or "unknown"
        )

        grouped[slot].append(
            record
        )

    return dict(
        sorted(
            grouped.items()
        )
    )


def sequence_analysis(
    records: list[dict]
) -> list[dict]:

    ordered = sorted(
        records,
        key=lambda record: (
            record.get(
                "result_date",
                ""
            ),
            record.get(
                "update_time",
                ""
            ),
            int(
                record.get(
                    "sequence",
                    1
                ) or 1
            )
        )
    )

    output = []

    previous = None

    for record in ordered:

        current = {
            "date": record.get(
                "result_date"
            ),
            "time": record.get(
                "update_time"
            ),
            "sequence": record.get(
                "sequence",
                1
            ),
            "value": record.get(
                "value"
            ),
            "previous_value": previous
        }

        output.append(
            current
        )

        previous = record.get(
            "value"
        )

    return output


def normalize_scores(
    scores: dict[int, float]
) -> dict[int, float]:

    if not scores:
        return {}

    minimum = min(
        scores.values()
    )

    maximum = max(
        scores.values()
    )

    if maximum == minimum:

        return {
            value: 1.0
            for value in scores
        }

    return {
        value: (
            score - minimum
        )
        /
        (
            maximum -
            minimum
        )
        for value, score in scores.items()
    }


def build_composite_ranking(
    records: list[dict],
    recent_window: int = 20
) -> list[dict]:

    if not records:
        return []

    frequency = frequency_analysis(
        records
    )

    recent = recent_frequency_analysis(
        records,
        recent_window
    )

    gaps = gap_analysis(
        records
    )

    recency = recency_scores(
        records
    )

    momentum = momentum_analysis(
        records,
        recent_window
    )

    repetition = repetition_analysis(
        records
    )

    values = set(
        frequency
    ).union(
        recent
    ).union(
        recency
    )

    total = max(
        1,
        sum(frequency.values())
    )

    recent_total = max(
        1,
        sum(recent.values())
    )

    raw_scores = {}

    components = {}

    for value in values:

        frequency_score = (
            frequency.get(
                value,
                0
            )
            /
            total
        )

        recent_score = (
            recent.get(
                value,
                0
            )
            /
            recent_total
        )

        recency_score = recency.get(
            value,
            0.0
        )

        momentum_score = max(
            0.0,
            momentum.get(
                value,
                0.0
            )
        )

        gap = gaps.get(
            value
        )

        if gap is None:
            gap_score = 0.0
        else:
            gap_score = (
                1.0 /
                (1.0 + gap)
            )

        repetition_score = (
            repetition.get(
                value,
                0
            )
            /
            max(
                1,
                len(records)
            )
        )

        # --------------------------------------------------------
        # V6 COMPOSITE SCORE
        #
        # Existing statistical methods remain represented.
        # Time-slot score is added separately by
        # build_time_slot_ranking().
        # --------------------------------------------------------

        score = (
            frequency_score * 0.25
            + recent_score * 0.20
            + recency_score * 0.20
            + momentum_score * 0.15
            + gap_score * 0.10
            + repetition_score * 0.10
        )

        raw_scores[value] = score

        components[value] = {
            "frequency": frequency_score,
            "recent": recent_score,
            "recency": recency_score,
            "momentum": momentum_score,
            "gap": gap_score,
            "repetition": repetition_score
        }

    normalized = normalize_scores(
        raw_scores
    )

    ranking = []

    for value, score in raw_scores.items():

        ranking.append(
            {
                "value": value,
                "score": round(
                    normalized.get(
                        value,
                        0.0
                    ),
                    6
                ),
                "raw_score": round(
                    score,
                    6
                ),
                "frequency": frequency.get(
                    value,
                    0
                ),
                "recent_frequency": recent.get(
                    value,
                    0
                ),
                "gap": gaps.get(
                    value
                ),
                "momentum": round(
                    momentum.get(
                        value,
                        0.0
                    ),
                    6
                ),
                "components": components[
                    value
                ]
            }
        )

    ranking.sort(
        key=lambda item: (
            -item["score"],
            item["value"]
        )
    )

    return ranking[:10]


def build_time_slot_ranking(
    records: list[dict],
    target_time: str,
    recent_window: int = 20
) -> dict[str, Any]:

    slot_records = [
        record
        for record in records
        if record.get(
            "update_time"
        ) == target_time
    ]

    ranking = build_composite_ranking(
        slot_records,
        recent_window
    )

    return {
        "update_time": target_time,
        "observations": len(
            slot_records
        ),
        "ranking": ranking,
        "records": slot_records
    }


def analyze_all_time_slots(
    records: list[dict],
    recent_window: int = 20
) -> dict[str, Any]:

    grouped = time_slot_records(
        records
    )

    output = {}

    for slot, slot_records in grouped.items():

        output[slot] = (
            build_time_slot_ranking(
                records,
                slot,
                recent_window
            )
        )

    return output


def analyze_market(
    records: list[dict],
    recent_window: int = 20
) -> dict[str, Any]:

    ordered = sorted(
        records,
        key=lambda record: (
            record.get(
                "result_date",
                ""
            ),
            record.get(
                "update_time",
                ""
            ),
            int(
                record.get(
                    "sequence",
                    1
                ) or 1
            )
        )
    )

    ranking = build_composite_ranking(
        ordered,
        recent_window
    )

    slots = analyze_all_time_slots(
        ordered,
        recent_window
    )

    stability = stability_analysis(
        ordered
    )

    return {
        "observations": len(
            ordered
        ),
        "ranking": ranking,
        "time_slots": slots,
        "position_distribution":
            digit_position_distribution(
                ordered
            ),
        "stability": stability,
        "sequence": sequence_analysis(
            ordered
        ),
        "disclaimer":
            "Statistical/experimental analysis only; "
            "scores are not guaranteed predictions."
    }


def walk_forward_backtest(
    records: list[dict],
    min_history: int = 10,
    top_n: int = 10
) -> dict[str, Any]:

    ordered = sorted(
        records,
        key=lambda record: (
            record.get(
                "result_date",
                ""
            ),
            record.get(
                "update_time",
                ""
            ),
            int(
                record.get(
                    "sequence",
                    1
                ) or 1
            )
        )
    )

    if len(ordered) <= min_history:

        return {
            "evaluations": 0,
            "hits": 0,
            "hit_rate": None,
            "details": [],
            "message":
                "Not enough historical observations "
                "for walk-forward backtesting."
        }

    details = []

    hits = 0

    for index in range(
        min_history,
        len(ordered)
    ):

        history = ordered[
            :index
        ]

        actual = ordered[
            index
        ]

        # --------------------------------------------------------
        # IMPORTANT:
        #
        # Only historical data before the actual observation is
        # used to calculate the ranking.
        # --------------------------------------------------------

        ranking = build_composite_ranking(
            history
        )

        candidates = [
            item["value"]
            for item in ranking[
                :top_n
            ]
        ]

        actual_values = parse_value(
            actual.get(
                "value"
            )
        )

        hit = any(
            value in candidates
            for value in actual_values
        )

        if hit:
            hits += 1

        details.append(
            {
                "index": index,
                "date": actual.get(
                    "result_date"
                ),
                "update_time":
                    actual.get(
                        "update_time"
                    ),
                "sequence":
                    actual.get(
                        "sequence",
                        1
                    ),
                "actual":
                    actual.get(
                        "value"
                    ),
                "candidates":
                    candidates,
                "hit":
                    hit
            }
        )

    evaluations = len(
        details
    )

    return {
        "evaluations":
            evaluations,
        "hits":
            hits,
        "hit_rate":
            round(
                hits / evaluations,
                6
            )
            if evaluations
            else None,
        "details":
            details,
        "method":
            "walk-forward / out-of-sample",
        "disclaimer":
            "Backtest performance is historical "
            "and does not guarantee future results."
    }


def slot_walk_forward_backtest(
    records: list[dict],
    target_time: str,
    min_history: int = 10,
    top_n: int = 10
) -> dict[str, Any]:

    slot_records = [
        record
        for record in records
        if record.get(
            "update_time"
        ) == target_time
    ]

    return walk_forward_backtest(
        slot_records,
        min_history,
        top_n
    )


def get_feature_summary(
    records: list[dict]
) -> dict[str, Any]:

    frequency = frequency_analysis(
        records
    )

    gaps = gap_analysis(
        records
    )

    recent = recent_frequency_analysis(
        records
    )

    recency = recency_scores(
        records
    )

    momentum = momentum_analysis(
        records
    )

    return {
        "frequency":
            frequency,
        "gaps":
            gaps,
        "recent_frequency":
            recent,
        "recency":
            recency,
        "momentum":
            momentum,
        "position_distribution":
            digit_position_distribution(
                records
            ),
        "stability":
            stability_analysis(
                records
            )
    }


# ============================================================
# OPTIONAL COMPATIBILITY HELPERS
# ============================================================

def analyze(
    values: list[str]
) -> dict[str, Any]:
    """
    Compatibility helper for older V5 code.

    Example:
        analyze(["459-81-227", "467-78-260"])
    """

    records = [
        {
            "value": value,
            "result_date": "",
            "update_time": "",
            "sequence": index + 1
        }
        for index, value
        in enumerate(values)
    ]

    return analyze_market(
        records
    )


def top_candidates(
    values: list[str],
    limit: int = 10
) -> list[dict]:

    records = [
        {
            "value": value,
            "result_date": "",
            "update_time": "",
            "sequence": index + 1
        }
        for index, value
        in enumerate(values)
    ]

    return build_composite_ranking(
        records
    )[:limit]
