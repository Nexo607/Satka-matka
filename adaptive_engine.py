from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import analytics


# ============================================================
# NEXO // ADAPTIVE ENGINE v6
# ============================================================
#
# Purpose:
#   Automatically calibrate the statistical feature weights
#   using historical walk-forward evaluation.
#
# Important:
#   - No future observations are used during each training step.
#   - Training is based on historical/out-of-sample evaluation.
#   - Results are experimental statistical scores.
#   - This is NOT a guarantee of future outcomes.
# ============================================================


BASE_DIR = Path(__file__).resolve().parent

MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(
    exist_ok=True
)


DEFAULT_MODEL_FILE = MODEL_DIR / "adaptive_model.json"


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


@dataclass
class ModelState:

    version: str = "v6-adaptive-1"

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

            self.weights = dict(
                DEFAULT_WEIGHTS
            )


# ============================================================
# WEIGHT UTILITIES
# ============================================================

def normalize_weights(
    weights: dict[str, float]
) -> dict[str, float]:

    clean = {}

    for feature in FEATURES:

        value = float(
            weights.get(
                feature,
                0.0
            )
        )

        if not math.isfinite(value):
            value = 0.0

        clean[feature] = max(
            0.0,
            value
        )

    total = sum(
        clean.values()
    )

    if total <= 0:

        return dict(
            DEFAULT_WEIGHTS
        )

    return {
        feature:
            clean[feature] / total
        for feature in FEATURES
    }


def round_weights(
    weights: dict[str, float]
) -> dict[str, float]:

    normalized = normalize_weights(
        weights
    )

    return {
        feature: round(
            normalized[feature],
            6
        )
        for feature in FEATURES
    }


# ============================================================
# MODEL STORAGE
# ============================================================

def model_path(
    market: str
) -> Path:

    safe_market = "".join(
        character
        if character.isalnum()
        else "_"
        for character in market
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

    path = model_path(
        market
    )

    payload = asdict(
        state
    )

    payload["weights"] = round_weights(
        state.weights or DEFAULT_WEIGHTS
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

    path = model_path(
        market
    )

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
                "v6-adaptive-1"
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
                )
            ),
            hits=int(
                payload.get(
                    "hits",
                    0
                )
            ),
            score=float(
                payload.get(
                    "score",
                    0.0
                )
            ),
            training_runs=int(
                payload.get(
                    "training_runs",
                    0
                )
            ),
            observations_used=int(
                payload.get(
                    "observations_used",
                    0
                )
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

    total = 0.0

    for feature in FEATURES:

        value = float(
            components.get(
                feature,
                0.0
            )
        )

        if not math.isfinite(value):

            value = 0.0

        total += (
            value *
            weights.get(
                feature,
                0.0
            )
        )

    return total


# ============================================================
# RANK WITH CUSTOM WEIGHTS
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

    base_ranking = (
        analytics.build_composite_ranking(
            records,
            recent_window
        )
    )

    rescored = []

    for item in base_ranking:

        score = feature_score(
            item,
            weights
        )

        copy_item = dict(
            item
        )

        copy_item[
            "adaptive_score"
        ] = round(
            score,
            8
        )

        copy_item[
            "adaptive_weights"
        ] = weights

        rescored.append(
            copy_item
        )

    rescored.sort(
        key=lambda item: (
            -item[
                "adaptive_score"
            ],
            item.get(
                "value",
                0
            )
        )
    )

    return rescored[
        :top_n
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
                )
                or 1
            ),
            int(
                record.get(
                    "id",
                    0
                )
                or 0
            )
        )
    )

    if len(ordered) <= min_history:

        return {
            "evaluations": 0,
            "hits": 0,
            "hit_rate": None,
            "score": 0.0,
            "weights": round_weights(
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
            item[
                "value"
            ]
            for item in ranking
        ]

        actual_values = (
            analytics.parse_value(
                actual.get(
                    "value"
                )
            )
        )

        hit = any(
            value in candidates
            for value in actual_values
        )

        if hit:

            hits += 1

        evaluations += 1

        details.append(
            {
                "index": index,
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
            }
        )

    hit_rate = (
        hits / evaluations
        if evaluations
        else 0.0
    )

    # Small-data penalty.
    #
    # A model with only a handful of evaluations should not
    # automatically beat a model with a much larger sample.
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
# CANDIDATE WEIGHT GENERATOR
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

    # Always test the current model.
    candidates.append(
        normalize_weights(
            base_weights
        )
    )

    # Always test the default model.
    candidates.append(
        normalize_weights(
            DEFAULT_WEIGHTS
        )
    )

    for _ in range(
        max(
            0,
            trials - 2
        )
    ):

        candidate = {}

        for feature in FEATURES:

            base = max(
                0.01,
                float(
                    base_weights.get(
                        feature,
                        0.1
                    )
                )
            )

            # Controlled mutation.
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
# AUTO TRAIN
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
            status="insufficient_data"
        )

        save_model(
            state,
            market
        )

        return {
            "ok": False,
            "market": market,
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
            min_history=min_history,
            top_n=top_n,
            recent_window=recent_window
        )

        if result[
            "status"
        ] != "evaluated":

            continue

        if (
            best_result is None
            or
            result[
                "score"
            ]
            >
            best_result[
                "score"
            ]
        ):

            best_result = result

    if best_result is None:

        state = ModelState(
            weights=base_weights,
            observations_used=len(
                records
            ),
            training_runs=(
                old_state.training_runs +
                1
            ),
            status="insufficient_data"
        )

        save_model(
            state,
            market
        )

        return {
            "ok": False,
            "market": market,
            "status":
                "insufficient_data"
        }

    trained_at = (
        __import__(
            "datetime"
        )
        .datetime.utcnow()
        .isoformat(
            timespec="seconds"
        )
        + "Z"
    )

    state = ModelState(
        weights=normalize_weights(
            best_result[
                "weights"
            ]
        ),
        evaluations=best_result[
            "evaluations"
        ],
        hits=best_result[
            "hits"
        ],
        score=best_result[
            "score"
        ],
        training_runs=(
            old_state.training_runs +
            1
        ),
        observations_used=len(
            records
        ),
        last_trained_at=trained_at,
        status="trained"
    )

    save_model(
        state,
        market
    )

    return {
        "ok": True,
        "market": market,
        "status": "trained",
        "model": asdict(
            state
        ),
        "training": {
            "trials": len(
                candidates
            ),
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
# AUTO TRAIN DECISION
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

    previous_count = (
        state.observations_used
    )

    return (
        current_observation_count -
        previous_count
        >=
        minimum_new_records
    )


# ============================================================
# GET CURRENT MODEL
# ============================================================

def get_model_status(
    market: str
) -> dict[str, Any]:

    state = load_model(
        market
    )

    return {
        "ok": True,
        "market": market,
        "model": asdict(
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
        top_n=top_n,
        recent_window=recent_window
    )

    return {
        "ok": True,
        "market": market,
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
# SAFE AUTO-TRAIN + ANALYZE
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

    if should_retrain(
        market,
        len(records),
        minimum_new_records
    ):

        training = train_model(
            market=market,
            records=records,
            min_history=min_history,
            top_n=top_n,
            recent_window=recent_window,
            trials=trials
        )

        retrained = True

    else:

        training = {
            "ok": True,
            "status":
                "model_current",
            "market":
                market
        }

    analysis_result = adaptive_analysis(
        market=market,
        records=records,
        top_n=top_n,
        recent_window=recent_window
    )

    return {
        "ok": True,
        "market": market,
        "retrained": retrained,
        "training": training,
        "analysis": analysis_result
    }
