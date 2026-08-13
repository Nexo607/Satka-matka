from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analytics


# ============================================================
# NEXO // ADAPTIVE ENGINE v6.1
# ============================================================
#
# Adaptive historical/statistical calibration engine.
#
# Features:
#   - frequency
#   - recent frequency
#   - recency
#   - momentum
#   - gap
#   - repetition
#   - walk-forward validation
#   - automatic weight calibration
#   - persistent per-market model
#
# IMPORTANT:
# This is historical/statistical analysis.
# It does NOT guarantee future outcomes.
# ============================================================


BASE_DIR = Path(__file__).resolve().parent

MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


FEATURES = [
    "frequency",
    "recent",
    "recency",
    "momentum",
    "gap",
    "repetition",
]


DEFAULT_WEIGHTS = {
    "frequency": 0.25,
    "recent": 0.20,
    "recency": 0.20,
    "momentum": 0.15,
    "gap": 0.10,
    "repetition": 0.10,
}


# ============================================================
# MODEL STATE
# ============================================================

@dataclass
class ModelState:

    version: str = "v6.1-adaptive"

    weights: dict[str, float] | None = None

    evaluations: int = 0

    hits: int = 0

    score: float = 0.0

    training_runs: int = 0

    observations_used: int = 0

    last_trained_at: str | None = None

    method: str = (
        "walk-forward adaptive calibration"
    )

    status: str = "untrained"

    def __post_init__(self):

        if self.weights is None:
            self.weights = dict(DEFAULT_WEIGHTS)


# ============================================================
# SAFE NUMERIC HELPERS
# ============================================================

def safe_float(value: Any, default: float = 0.0) -> float:

    try:

        number = float(value)

        if math.isfinite(number):
            return number

    except Exception:
        pass

    return default


def clamp(
    value: float,
    minimum: float = 0.0,
    maximum: float = 1.0
) -> float:

    return max(
        minimum,
        min(
            maximum,
            value
        )
    )


# ============================================================
# WEIGHT UTILITIES
# ============================================================

def normalize_weights(
    weights: dict[str, float] | None
) -> dict[str, float]:

    weights = weights or {}

    clean = {}

    for feature in FEATURES:

        value = safe_float(
            weights.get(feature, 0.0)
        )

        clean[feature] = max(
            0.0,
            value
        )

    total = sum(clean.values())

    if total <= 0:

        return dict(DEFAULT_WEIGHTS)

    return {
        feature:
            clean[feature] / total
        for feature in FEATURES
    }


def round_weights(
    weights: dict[str, float] | None
) -> dict[str, float]:

    normalized = normalize_weights(
        weights
    )

    return {
        feature:
            round(
                normalized[feature],
                6
            )
        for feature in FEATURES
    }


# ============================================================
# MODEL FILE
# ============================================================

def model_path(
    market: str
) -> Path:

    safe_market = "".join(
        char if char.isalnum() else "_"
        for char in str(market)
    ).strip("_")

    if not safe_market:
        safe_market = "default"

    return (
        MODEL_DIR /
        f"{safe_market.lower()}_adaptive.json"
    )


def save_model(
    state: ModelState,
    market: str
):

    path = model_path(market)

    payload = asdict(state)

    payload["weights"] = round_weights(
        state.weights
    )

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )


def load_model(
    market: str
) -> ModelState:

    path = model_path(market)

    if not path.exists():
        return ModelState()

    try:

        payload = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return ModelState(

            version=payload.get(
                "version",
                "v6.1-adaptive"
            ),

            weights=normalize_weights(
                payload.get(
                    "weights",
                    DEFAULT_WEIGHTS
                )
            ),

            evaluations=int(
                payload.get(
                    "evaluations",
                    0
                ) or 0
            ),

            hits=int(
                payload.get(
                    "hits",
                    0
                ) or 0
            ),

            score=safe_float(
                payload.get(
                    "score",
                    0.0
                )
            ),

            training_runs=int(
                payload.get(
                    "training_runs",
                    0
                ) or 0
            ),

            observations_used=int(
                payload.get(
                    "observations_used",
                    0
                ) or 0
            ),

            last_trained_at=payload.get(
                "last_trained_at"
            ),

            method=payload.get(
                "method",
                "walk-forward adaptive calibration"
            ),

            status=payload.get(
                "status",
                "loaded"
            )
        )

    except Exception:

        return ModelState()


# ============================================================
# RECORD ORDERING
# ============================================================

def ordered_records(
    records: list[dict]
) -> list[dict]:

    def sort_key(record):

        return (
            str(
                record.get(
                    "result_date",
                    ""
                ) or ""
            ),

            str(
                record.get(
                    "update_time",
                    ""
                ) or ""
            ),

            int(
                record.get(
                    "sequence",
                    1
                ) or 1
            ),

            int(
                record.get(
                    "id",
                    0
                ) or 0
            )
        )

    return sorted(
        records,
        key=sort_key
    )


# ============================================================
# VALUE EXTRACTION
# ============================================================

def normalize_value(
    value: Any
) -> str:

    if value is None:
        return ""

    text = str(value).strip()

    return text


def values_from_record(
    record: dict
) -> list[str]:

    raw = record.get("value")

    if raw is None:
        return []

    # First use analytics parser when available.
    try:

        parsed = analytics.parse_value(
            raw
        )

        if isinstance(parsed, (list, tuple, set)):

            values = [
                normalize_value(x)
                for x in parsed
            ]

            return [
                x for x in values
                if x
            ]

        if parsed is not None:

            text = normalize_value(
                parsed
            )

            if text:
                return [text]

    except Exception:
        pass

    text = normalize_value(raw)

    return [text] if text else []


# ============================================================
# HISTORICAL FEATURE EXTRACTION
# ============================================================

def build_feature_records(
    records: list[dict],
    recent_window: int = 20
) -> list[dict]:

    ordered = ordered_records(records)

    if not ordered:
        return []

    recent_window = max(
        1,
        int(recent_window)
    )

    all_values = []

    for record in ordered:

        all_values.extend(
            values_from_record(
                record
            )
        )

    if not all_values:
        return []

    total = len(all_values)

    counter = {}

    for value in all_values:

        counter[value] = (
            counter.get(value, 0) + 1
        )

    recent_values = all_values[
        -min(
            recent_window,
            total
        ):
    ]

    recent_counter = {}

    for value in recent_values:

        recent_counter[value] = (
            recent_counter.get(value, 0) + 1
        )

    last_seen = {}

    for index, value in enumerate(all_values):

        last_seen[value] = index

    rows = []

    unique_count = max(
        1,
        len(counter)
    )

    for value in counter:

        frequency = counter[value]

        recent_frequency = (
            recent_counter.get(
                value,
                0
            )
        )

        last_index = last_seen[value]

        gap = (
            total -
            1 -
            last_index
        )

        recency = math.exp(
            -gap /
            max(
                10.0,
                total * 0.08
            )
        )

        recent_ratio = (
            recent_frequency /
            max(
                1,
                min(
                    recent_window,
                    total
                )
            )
        )

        historical_ratio = (
            frequency /
            max(
                1,
                total
            )
        )

        momentum = (
            recent_ratio /
            max(
                historical_ratio,
                1.0 / total
            )
        )

        momentum = clamp(
            momentum /
            3.0
        )

        # Repetition:
        # How strongly the value repeats historically
        # compared with a one-off observation.
        repetition = clamp(
            frequency /
            max(
                1.0,
                total / unique_count
            )
        )

        # Frequency normalization.
        frequency_score = clamp(
            frequency /
            max(
                1.0,
                max(counter.values())
            )
        )

        # Gap score intentionally uses a smooth decay.
        gap_score = 1.0 / (
            1.0 +
            math.log1p(
                max(
                    0,
                    gap
                )
            )
        )

        rows.append({

            "value":
                value,

            "frequency":
                frequency,

            "recent_frequency":
                recent_frequency,

            "gap":
                gap,

            "recency":
                clamp(
                    recency
                ),

            "frequency_score":
                frequency_score,

            "recent":
                recent_ratio,

            "momentum":
                momentum,

            "gap_score":
                clamp(
                    gap_score
                ),

            "repetition":
                repetition,

        })

    return rows


# ============================================================
# FEATURE SCORE
# ============================================================

def feature_score(
    item: dict[str, Any],
    weights: dict[str, float]
) -> float:

    components = item.get(
        "components",
        {}
    )

    # Support both:
    # item["components"]["frequency"]
    # and direct feature fields.
    def component(
        feature: str,
        aliases: tuple[str, ...] = ()
    ):

        if feature in components:
            return safe_float(
                components.get(feature)
            )

        for alias in aliases:

            if alias in components:

                return safe_float(
                    components.get(alias)
                )

        if feature in item:

            return safe_float(
                item.get(feature)
            )

        for alias in aliases:

            if alias in item:

                return safe_float(
                    item.get(alias)
                )

        return 0.0

    values = {

        "frequency":
            component(
                "frequency",
                (
                    "frequency_score",
                )
            ),

        "recent":
            component(
                "recent",
                (
                    "recent_frequency",
                    "recent_score"
                )
            ),

        "recency":
            component(
                "recency"
            ),

        "momentum":
            component(
                "momentum"
            ),

        "gap":
            component(
                "gap_score",
                (
                    "gap"
                )
            ),

        "repetition":
            component(
                "repetition"
            )
    }

    total = 0.0

    for feature in FEATURES:

        value = clamp(
            values.get(
                feature,
                0.0
            )
        )

        total += (
            value *
            weights.get(
                feature,
                0.0
            )
        )

    return total


# ============================================================
# RANKING
# ============================================================

def rank_with_weights(
    records: list[dict],
    weights: dict[str, float],
    top_n: int = 10,
    recent_window: int = 20
) -> list[dict]:

    weights = normalize_weights(
        weights
    )

    feature_rows = build_feature_records(
        records,
        recent_window
    )

    rescored = []

    for item in feature_rows:

        components = {

            "frequency":
                item[
                    "frequency_score"
                ],

            "recent":
                item[
                    "recent"
                ],

            "recency":
                item[
                    "recency"
                ],

            "momentum":
                item[
                    "momentum"
                ],

            "gap":
                item[
                    "gap_score"
                ],

            "repetition":
                item[
                    "repetition"
                ],
        }

        enriched = {

            "value":
                item["value"],

            "frequency":
                item["frequency"],

            "recent_frequency":
                item[
                    "recent_frequency"
                ],

            "gap":
                item["gap"],

            "components":
                components,

            "adaptive_score":
                round(
                    feature_score(
                        {
                            "components":
                                components
                        },
                        weights
                    ) * 100,
                    6
                ),

            "adaptive_weights":
                weights,
        }

        rescored.append(
            enriched
        )

    rescored.sort(
        key=lambda item: (
            -safe_float(
                item.get(
                    "adaptive_score",
                    0
                )
            ),

            -int(
                item.get(
                    "frequency",
                    0
                )
            ),

            int(
                item.get(
                    "gap",
                    0
                )
            ),

            str(
                item.get(
                    "value",
                    ""
                )
            )
        )
    )

    return rescored[
        :max(
            1,
            int(top_n)
        )
    ]


# ============================================================
# WALK-FORWARD EVALUATION
# ============================================================

def evaluate_weights(
    records: list[dict],
    weights: dict[str, float],
    min_history: int = 10,
    top_n: int = 10,
    recent_window: int = 20
) -> dict[str, Any]:

    ordered = ordered_records(
        records
    )

    if len(ordered) <= min_history:

        return {

            "evaluations": 0,

            "hits": 0,

            "hit_rate": None,

            "score": 0.0,

            "weights":
                round_weights(
                    weights
                ),

            "status":
                "insufficient_data"
        }

    hits = 0

    evaluations = 0

    details = []

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

        ranking = rank_with_weights(
            history,
            weights,
            top_n=top_n,
            recent_window=recent_window
        )

        candidates = [

            normalize_value(
                item.get(
                    "value"
                )
            )

            for item in ranking
        ]

        actual_values = (
            values_from_record(
                actual
            )
        )

        hit = any(
            value in candidates
            for value in actual_values
        )

        if hit:
            hits += 1

        evaluations += 1

        details.append({

            "index":
                index,

            "date":
                actual.get(
                    "result_date"
                ),

            "update_time":
                actual.get(
                    "update_time"
                ),

            "actual":
                actual.get(
                    "value"
                ),

            "candidates":
                candidates,

            "hit":
                hit
        })

    hit_rate = (
        hits / evaluations
        if evaluations
        else 0.0
    )

    sample_factor = min(
        1.0,
        evaluations / 50.0
    )

    score = (
        hit_rate *
        sample_factor
    )

    return {

        "evaluations":
            evaluations,

        "hits":
            hits,

        "hit_rate":
            round(
                hit_rate,
                6
            ),

        "score":
            round(
                score,
                6
            ),

        "weights":
            round_weights(
                weights
            ),

        "details":
            details,

        "status":
            "evaluated"
    }


# ============================================================
# CANDIDATE GENERATOR
# ============================================================

def generate_candidates(
    base_weights: dict[str, float],
    trials: int = 60,
    seed: int | None = None
) -> list[dict[str, float]]:

    rng = random.Random(
        seed
    )

    candidates = []

    candidates.append(
        normalize_weights(
            base_weights
        )
    )

    candidates.append(
        normalize_weights(
            DEFAULT_WEIGHTS
        )
    )

    trials = max(
        2,
        int(trials)
    )

    for _ in range(
        trials - 2
    ):

        candidate = {}

        for feature in FEATURES:

            base = max(
                0.01,
                safe_float(
                    base_weights.get(
                        feature,
                        0.1
                    ),
                    0.1
                )
            )

            multiplier = rng.uniform(
                0.55,
                1.45
            )

            candidate[
                feature
            ] = (
                base *
                multiplier
            )

        candidates.append(
            normalize_weights(
                candidate
            )
        )

    return candidates


# ============================================================
# TRAIN MODEL
# ============================================================

def train_model(
    market: str,
    records: list[dict],
    min_history: int = 10,
    top_n: int = 10,
    recent_window: int = 20,
    trials: int = 60,
    seed: int | None = None
) -> dict[str, Any]:

    if not records:

        state = ModelState(
            status=
                "insufficient_data"
        )

        save_model(
            state,
            market
        )

        return {

            "ok": False,

            "market":
                market,

            "status":
                "insufficient_data",

            "message":
                "No historical observations available."
        }

    old_state = load_model(
        market
    )

    base_weights = normalize_weights(
        old_state.weights
        or DEFAULT_WEIGHTS
    )

    candidates = generate_candidates(
        base_weights,
        trials=trials,
        seed=seed
    )

    best_result = None

    for candidate in candidates:

        result = evaluate_weights(

            records,

            candidate,

            min_history=
                min_history,

            top_n=
                top_n,

            recent_window=
                recent_window
        )

        if result[
            "status"
        ] != "evaluated":

            continue

        if (
            best_result is None
            or
            result["score"]
            >
            best_result["score"]
        ):

            best_result = result

    if best_result is None:

        state = ModelState(

            weights=
                base_weights,

            observations_used=
                len(records),

            training_runs=(
                old_state.training_runs +
                1
            ),

            status=
                "insufficient_data"
        )

        save_model(
            state,
            market
        )

        return {

            "ok": False,

            "market":
                market,

            "status":
                "insufficient_data",

            "message":
                "Not enough historical data for training."
        }

    trained_at = (
        datetime.now(
            timezone.utc
        )
        .isoformat(
            timespec="seconds"
        )
    )

    state = ModelState(

        weights=
            normalize_weights(
                best_result[
                    "weights"
                ]
            ),

        evaluations=
            best_result[
                "evaluations"
            ],

        hits=
            best_result[
                "hits"
            ],

        score=
            best_result[
                "score"
            ],

        training_runs=(
            old_state.training_runs +
            1
        ),

        observations_used=
            len(records),

        last_trained_at=
            trained_at,

        status=
            "trained"
    )

    save_model(
        state,
        market
    )

    return {

        "ok":
            True,

        "market":
            market,

        "status":
            "trained",

        "model":
            asdict(
                state
            ),

        "training": {

            "trials":
                len(candidates),

            "evaluations":
                best_result[
                    "evaluations"
                ],

            "hits":
                best_result[
                    "hits"
                ],

            "validated_score":
                best_result[
                    "score"
                ],

            "hit_rate":
                best_result[
                    "hit_rate"
                ]
        },

        "message":
            "Adaptive model recalibrated using "
            "walk-forward historical evaluation."
    }


# ============================================================
# RETRAIN DECISION
# ============================================================

def should_retrain(
    market: str,
    current_observation_count: int,
    minimum_new_records: int = 5
) -> bool:

    state = load_model(
        market
    )

    if state.status != "trained":
        return True

    previous_count = max(
        0,
        state.observations_used
    )

    return (
        current_observation_count -
        previous_count
        >=
        max(
            1,
            int(
                minimum_new_records
            )
        )
    )


# ============================================================
# MODEL STATUS
# ============================================================

def get_model_status(
    market: str
) -> dict[str, Any]:

    state = load_model(
        market
    )

    return {

        "ok":
            True,

        "market":
            market,

        "model":
            asdict(
                state
            ),

        "weights":
            round_weights(
                state.weights
                or DEFAULT_WEIGHTS
            )
    }


# ============================================================
# ADAPTIVE ANALYSIS
# ============================================================

def adaptive_analysis(
    market: str,
    records: list[dict],
    top_n: int = 10,
    recent_window: int = 20
) -> dict[str, Any]:

    state = load_model(
        market
    )

    weights = normalize_weights(
        state.weights
        or DEFAULT_WEIGHTS
    )

    ranking = rank_with_weights(

        records,

        weights,

        top_n=
            top_n,

        recent_window=
            recent_window
    )

    return {

        "ok":
            True,

        "market":
            market,

        "model_status":
            state.status,

        "model_version":
            state.version,

        "observations":
            len(records),

        "weights":
            weights,

        "ranking":
            ranking,

        "trained_score":
            state.score,

        "training_runs":
            state.training_runs,

        "last_trained_at":
            state.last_trained_at,

        "disclaimer":
            "Historical/statistical model calibration only. "
            "Scores are experimental and do not guarantee "
            "future outcomes."
    }


# ============================================================
# AUTO UPDATE
# ============================================================

def auto_update(
    market: str,
    records: list[dict],
    minimum_new_records: int = 5,
    min_history: int = 10,
    top_n: int = 10,
    recent_window: int = 20,
    trials: int = 60
) -> dict[str, Any]:

    retrained = False

    training = {

        "ok":
            True,

        "status":
            "model_current",

        "market":
            market
    }

    if should_retrain(

        market,

        len(records),

        minimum_new_records
    ):

        training = train_model(

            market=
                market,

            records=
                records,

            min_history=
                min_history,

            top_n=
                top_n,

            recent_window=
                recent_window,

            trials=
                trials
        )

        retrained = True

    analysis_result = adaptive_analysis(

        market=
            market,

        records=
            records,

        top_n=
            top_n,

        recent_window=
            recent_window
    )

    return {

        "ok":
            True,

        "market":
            market,

        "retrained":
            retrained,

        "training":
            training,

        "analysis":
            analysis_result
    }


# ============================================================
# TIME-SLOT ANALYSIS
# ============================================================

def analyze_time_slot(
    market: str,
    records: list[dict],
    update_time: str,
    top_n: int = 10,
    recent_window: int = 20
) -> dict[str, Any]:

    slot_records = [

        record

        for record in records

        if str(
            record.get(
                "update_time",
                ""
            )
        ) == str(update_time)
    ]

    result = adaptive_analysis(

        market=
            market,

        records=
            slot_records,

        top_n=
            top_n,

        recent_window=
            recent_window
    )

    result["time_slot"] = update_time

    result["slot_observations"] = len(
        slot_records
    )

    return result


# ============================================================
# MARKET TRAINING SUMMARY
# ============================================================

def training_summary(
    market: str,
    records: list[dict]
) -> dict[str, Any]:

    state = load_model(
        market
    )

    return {

        "market":
            market,

        "model_version":
            state.version,

        "status":
            state.status,

        "observations":
            len(records),

        "observations_used":
            state.observations_used,

        "training_runs":
            state.training_runs,

        "evaluations":
            state.evaluations,

        "hits":
            state.hits,

        "validated_score":
            state.score,

        "weights":
            round_weights(
                state.weights
            ),

        "last_trained_at":
            state.last_trained_at
    }
