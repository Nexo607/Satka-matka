from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# NEXO // HISTORICAL ANALYTICS V6
# DATABASE LAYER
#
# Compatibility goals:
#   - Preserve existing V5/V6 results
#   - Preserve existing functions
#   - Preserve time-slot support
#   - Add complete observation history
#   - Support Kalyan intraday slots such as 16:02 / 18:02
#   - Support adaptive training later
# ============================================================


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(
    exist_ok=True
)

DB_PATH = DATA_DIR / "nexo.db"


# ============================================================
# CONNECTION
# ============================================================

def get_connection():

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    connection.execute(
        "PRAGMA foreign_keys=ON"
    )

    return connection


# ============================================================
# SCHEMA HELPERS
# ============================================================

def _column_exists(
    db,
    table: str,
    column: str
) -> bool:

    rows = db.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(
        row["name"] == column
        for row in rows
    )


def _add_column_if_missing(
    db,
    table: str,
    column: str,
    definition: str
):

    if not _column_exists(
        db,
        table,
        column
    ):

        db.execute(
            f"""
            ALTER TABLE {table}
            ADD COLUMN {column} {definition}
            """
        )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    with get_connection() as db:

        # ====================================================
        # EXISTING V5/V6 RESULTS TABLE
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS results (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                value TEXT NOT NULL,

                source_url TEXT NOT NULL,

                source_index INTEGER,

                fetched_at TEXT NOT NULL,

                result_date TEXT,

                update_time TEXT,

                sequence INTEGER DEFAULT 1,

                note TEXT,

                UNIQUE(
                    market,
                    value,
                    source_index
                )
            )
        """)

        # ====================================================
        # V6 MIGRATION
        # ====================================================

        _add_column_if_missing(
            db,
            "results",
            "result_date",
            "TEXT"
        )

        _add_column_if_missing(
            db,
            "results",
            "update_time",
            "TEXT"
        )

        _add_column_if_missing(
            db,
            "results",
            "sequence",
            "INTEGER DEFAULT 1"
        )

        _add_column_if_missing(
            db,
            "results",
            "note",
            "TEXT"
        )

        # ====================================================
        # RESULT INDEXES
        # ====================================================

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_results_market_date_time
            ON results(
                market,
                result_date,
                update_time,
                sequence
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_results_market_value
            ON results(
                market,
                value
            )
        """)

        # ====================================================
        # V6 SYNC RUNS
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS sync_runs (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                started_at TEXT NOT NULL,

                finished_at TEXT NOT NULL,

                status TEXT NOT NULL,

                records_found INTEGER DEFAULT 0,

                records_added INTEGER DEFAULT 0,

                error TEXT
            )
        """)

        # ====================================================
        # V6 MARKET TIME SLOTS
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS market_time_slots (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                update_time TEXT NOT NULL,

                enabled INTEGER NOT NULL DEFAULT 1,

                created_at TEXT NOT NULL,

                UNIQUE(
                    market,
                    update_time
                )
            )
        """)

        # ====================================================
        # NEW V6 OBSERVATION TABLE
        #
        # IMPORTANT:
        #
        # This table stores EVERY observation.
        #
        # Unlike the old results table, it does not collapse
        # repeated values into one historical record.
        #
        # Example:
        #
        # 2026-08-10 16:02 -> 459
        # 2026-08-11 16:02 -> 459
        # 2026-08-12 16:02 -> 459
        #
        # All three remain separate observations.
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS observations (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                value TEXT NOT NULL,

                result_date TEXT,

                update_time TEXT,

                sequence INTEGER DEFAULT 1,

                source_url TEXT,

                source_index INTEGER,

                fetched_at TEXT NOT NULL,

                note TEXT,

                observation_hash TEXT UNIQUE
            )
        """)

        # ====================================================
        # OBSERVATION INDEXES
        # ====================================================

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_observations_market
            ON observations(
                market
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_observations_market_date
            ON observations(
                market,
                result_date
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_observations_market_time
            ON observations(
                market,
                update_time
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_observations_market_date_time
            ON observations(
                market,
                result_date,
                update_time,
                sequence
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_observations_market_value
            ON observations(
                market,
                value
            )
        """)

        # ====================================================
        # ADAPTIVE TRAINING RUNS
        #
        # Stores metadata for future self-training.
        # No prediction is stored as guaranteed truth.
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS training_runs (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                started_at TEXT NOT NULL,

                finished_at TEXT,

                records_used INTEGER DEFAULT 0,

                train_window INTEGER DEFAULT 0,

                validation_window INTEGER DEFAULT 0,

                model_version TEXT,

                status TEXT NOT NULL,

                score REAL,

                notes TEXT
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_training_market
            ON training_runs(
                market,
                id
            )
        """)

        # ====================================================
        # MODEL PARAMETERS
        #
        # Stores learned/validated feature weights later.
        # ====================================================

        db.execute("""
            CREATE TABLE IF NOT EXISTS model_parameters (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                market TEXT NOT NULL,

                model_version TEXT NOT NULL,

                parameter_name TEXT NOT NULL,

                parameter_value REAL NOT NULL,

                validation_score REAL,

                created_at TEXT NOT NULL,

                UNIQUE(
                    market,
                    model_version,
                    parameter_name
                )
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_model_parameters_market
            ON model_parameters(
                market,
                created_at
            )
        """)

        # ====================================================
        # MIGRATE OLD RESULTS -> OBSERVATIONS
        #
        # Existing data is preserved.
        # ====================================================

        old_rows = db.execute("""
            SELECT
                id,
                market,
                value,
                source_url,
                source_index,
                fetched_at,
                result_date,
                update_time,
                sequence,
                note
            FROM results
        """).fetchall()

        for row in old_rows:

            observation_hash = make_observation_hash(
                market=row["market"],
                value=row["value"],
                result_date=row["result_date"],
                update_time=row["update_time"],
                sequence=row["sequence"],
                source_index=row["source_index"]
            )

            db.execute(
                """
                INSERT OR IGNORE INTO observations
                (
                    market,
                    value,
                    result_date,
                    update_time,
                    sequence,
                    source_url,
                    source_index,
                    fetched_at,
                    note,
                    observation_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["market"],
                    row["value"],
                    row["result_date"],
                    row["update_time"],
                    row["sequence"] or 1,
                    row["source_url"],
                    row["source_index"],
                    row["fetched_at"],
                    row["note"],
                    observation_hash
                )
            )

        # ====================================================
        # MIGRATE OLD TIMESTAMPS
        # ====================================================

        rows = db.execute("""
            SELECT
                id,
                fetched_at,
                result_date,
                update_time,
                sequence
            FROM results
            WHERE
                result_date IS NULL
                OR update_time IS NULL
        """).fetchall()

        for row in rows:

            fetched_at = row["fetched_at"]

            result_date = row["result_date"]
            update_time = row["update_time"]

            if fetched_at:

                try:

                    clean_value = str(
                        fetched_at
                    ).replace(
                        "Z",
                        "+00:00"
                    )

                    parsed = datetime.fromisoformat(
                        clean_value
                    )

                    if not result_date:

                        result_date = parsed.strftime(
                            "%Y-%m-%d"
                        )

                    if not update_time:

                        update_time = parsed.strftime(
                            "%H:%M"
                        )

                except Exception:

                    pass

            db.execute(
                """
                UPDATE results
                SET
                    result_date = ?,
                    update_time = ?,
                    sequence = COALESCE(
                        sequence,
                        1
                    )
                WHERE id = ?
                """,
                (
                    result_date,
                    update_time,
                    row["id"]
                )
            )

        db.commit()


# ============================================================
# HASHING
# ============================================================

def make_observation_hash(
    market: str,
    value: str,
    result_date: str | None,
    update_time: str | None,
    sequence: int | None,
    source_index: int | None
) -> str:

    import hashlib

    raw = "|".join([
        str(market or ""),
        str(value or ""),
        str(result_date or ""),
        str(update_time or ""),
        str(sequence or 1),
        str(source_index if source_index is not None else "")
    ])

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# INSERT RESULTS
# ============================================================

def insert_results(
    market: str,
    values: list[str],
    source_url: str,
    fetched_at: str,
    result_date: str | None = None,
    update_time: str | None = None,
    sequence_start: int = 1,
    note: str | None = None
) -> int:

    added = 0

    if not result_date or not update_time:

        try:

            clean_value = str(
                fetched_at
            ).replace(
                "Z",
                "+00:00"
            )

            parsed = datetime.fromisoformat(
                clean_value
            )

            if not result_date:

                result_date = parsed.strftime(
                    "%Y-%m-%d"
                )

            if not update_time:

                update_time = parsed.strftime(
                    "%H:%M"
                )

        except Exception:

            pass

    with get_connection() as db:

        for offset, value in enumerate(values):

            sequence = (
                sequence_start +
                offset
            )

            # ----------------------------------------------
            # Preserve existing V6 behaviour.
            # ----------------------------------------------

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO results
                (
                    market,
                    value,
                    source_url,
                    source_index,
                    fetched_at,
                    result_date,
                    update_time,
                    sequence,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market,
                    value,
                    source_url,
                    offset,
                    fetched_at,
                    result_date,
                    update_time,
                    sequence,
                    note
                )
            )

            if cursor.rowcount:

                added += 1

            # ----------------------------------------------
            # ALSO STORE COMPLETE OBSERVATION.
            # ----------------------------------------------

            observation_hash = make_observation_hash(
                market=market,
                value=value,
                result_date=result_date,
                update_time=update_time,
                sequence=sequence,
                source_index=offset
            )

            db.execute(
                """
                INSERT OR IGNORE INTO observations
                (
                    market,
                    value,
                    result_date,
                    update_time,
                    sequence,
                    source_url,
                    source_index,
                    fetched_at,
                    note,
                    observation_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    market,
                    value,
                    result_date,
                    update_time,
                    sequence,
                    source_url,
                    offset,
                    fetched_at,
                    note,
                    observation_hash
                )
            )

        db.commit()

    return added


# ============================================================
# INSERT SINGLE TIME-SLOT RESULT
# ============================================================

def insert_time_slot_result(
    market: str,
    value: str,
    result_date: str,
    update_time: str,
    source_url: str = "",
    source_index: int | None = None,
    sequence: int = 1,
    note: str | None = None,
    fetched_at: str | None = None
) -> int:

    if not fetched_at:

        fetched_at = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

    observation_hash = make_observation_hash(
        market=market,
        value=value,
        result_date=result_date,
        update_time=update_time,
        sequence=sequence,
        source_index=source_index
    )

    with get_connection() as db:

        # ------------------------------------------------
        # Check observation layer.
        # ------------------------------------------------

        existing = db.execute(
            """
            SELECT id
            FROM observations
            WHERE observation_hash = ?
            LIMIT 1
            """,
            (
                observation_hash,
            )
        ).fetchone()

        if existing:

            return 0

        # ------------------------------------------------
        # Existing V6 results table.
        # ------------------------------------------------

        db.execute(
            """
            INSERT OR IGNORE INTO results
            (
                market,
                value,
                source_url,
                source_index,
                fetched_at,
                result_date,
                update_time,
                sequence,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                value,
                source_url,
                source_index,
                fetched_at,
                result_date,
                update_time,
                sequence,
                note
            )
        )

        # ------------------------------------------------
        # Complete observation.
        # ------------------------------------------------

        db.execute(
            """
            INSERT INTO observations
            (
                market,
                value,
                result_date,
                update_time,
                sequence,
                source_url,
                source_index,
                fetched_at,
                note,
                observation_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                value,
                result_date,
                update_time,
                sequence,
                source_url,
                source_index,
                fetched_at,
                note,
                observation_hash
            )
        )

        db.commit()

    return 1


# ============================================================
# GET VALUES
# ============================================================

def get_values(
    market: str
) -> list[str]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT value
            FROM observations
            WHERE market = ?
            ORDER BY
                COALESCE(result_date, ''),
                COALESCE(update_time, ''),
                sequence,
                id
            """,
            (
                market,
            )
        ).fetchall()

    return [
        row["value"]
        for row in rows
    ]


# ============================================================
# GET RESULT ROWS
# ============================================================

def get_result_rows(
    market: str
) -> list[dict[str, Any]]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                id,
                market,
                value,
                source_url,
                source_index,
                fetched_at,
                result_date,
                update_time,
                sequence,
                note
            FROM observations
            WHERE market = ?
            ORDER BY
                COALESCE(result_date, ''),
                COALESCE(update_time, ''),
                sequence,
                id
            """,
            (
                market,
            )
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# GET TIME-SLOT OBSERVATIONS
# ============================================================

def get_time_slot_rows(
    market: str,
    update_time: str,
    limit: int | None = None
) -> list[dict[str, Any]]:

    query = """
        SELECT
            id,
            market,
            value,
            source_url,
            source_index,
            fetched_at,
            result_date,
            update_time,
            sequence,
            note
        FROM observations
        WHERE
            market = ?
            AND update_time = ?
        ORDER BY
            COALESCE(result_date, ''),
            sequence,
            id
    """

    params: list[Any] = [
        market,
        update_time
    ]

    if limit is not None:

        query += " LIMIT ?"

        params.append(
            int(limit)
        )

    with get_connection() as db:

        rows = db.execute(
            query,
            tuple(params)
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# GET AVAILABLE TIME SLOTS
# ============================================================

def get_available_time_slots(
    market: str
) -> list[str]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT DISTINCT update_time
            FROM observations
            WHERE
                market = ?
                AND update_time IS NOT NULL
                AND update_time != ''
            ORDER BY update_time
            """,
            (
                market,
            )
        ).fetchall()

    return [
        row["update_time"]
        for row in rows
    ]


# ============================================================
# GET DATE ROWS
# ============================================================

def get_date_rows(
    market: str,
    result_date: str
) -> list[dict[str, Any]]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                id,
                market,
                value,
                source_url,
                source_index,
                fetched_at,
                result_date,
                update_time,
                sequence,
                note
            FROM observations
            WHERE
                market = ?
                AND result_date = ?
            ORDER BY
                update_time,
                sequence,
                id
            """,
            (
                market,
                result_date
            )
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# OBSERVATION STATISTICS
# ============================================================

def get_observation_stats(
    market: str
) -> dict[str, Any]:

    with get_connection() as db:

        total = db.execute(
            """
            SELECT COUNT(*)
            FROM observations
            WHERE market = ?
            """,
            (
                market,
            )
        ).fetchone()[0]

        unique_values = db.execute(
            """
            SELECT COUNT(
                DISTINCT value
            )
            FROM observations
            WHERE market = ?
            """,
            (
                market,
            )
        ).fetchone()[0]

        dates = db.execute(
            """
            SELECT COUNT(
                DISTINCT result_date
            )
            FROM observations
            WHERE
                market = ?
                AND result_date IS NOT NULL
            """,
            (
                market,
            )
        ).fetchone()[0]

        slots = db.execute(
            """
            SELECT COUNT(
                DISTINCT update_time
            )
            FROM observations
            WHERE
                market = ?
                AND update_time IS NOT NULL
            """,
            (
                market,
            )
        ).fetchone()[0]

    return {
        "market": market,
        "observations": total,
        "unique_values": unique_values,
        "dates": dates,
        "time_slots": slots
    }


# ============================================================
# TRAINING RUNS
# ============================================================

def save_training_run(
    market: str,
    started_at: str,
    finished_at: str | None,
    status: str,
    records_used: int = 0,
    train_window: int = 0,
    validation_window: int = 0,
    model_version: str | None = None,
    score: float | None = None,
    notes: str | None = None
):

    with get_connection() as db:

        db.execute(
            """
            INSERT INTO training_runs
            (
                market,
                started_at,
                finished_at,
                records_used,
                train_window,
                validation_window,
                model_version,
                status,
                score,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                started_at,
                finished_at,
                records_used,
                train_window,
                validation_window,
                model_version,
                status,
                score,
                notes
            )
        )

        db.commit()


def latest_training_run(
    market: str
):

    with get_connection() as db:

        row = db.execute(
            """
            SELECT *
            FROM training_runs
            WHERE market = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                market,
            )
        ).fetchone()

    return dict(row) if row else None


# ============================================================
# MODEL PARAMETERS
# ============================================================

def save_model_parameter(
    market: str,
    model_version: str,
    parameter_name: str,
    parameter_value: float,
    validation_score: float | None = None
):

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as db:

        db.execute(
            """
            INSERT INTO model_parameters
            (
                market,
                model_version,
                parameter_name,
                parameter_value,
                validation_score,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(
                market,
                model_version,
                parameter_name
            )

            DO UPDATE SET
                parameter_value =
                    excluded.parameter_value,

                validation_score =
                    excluded.validation_score,

                created_at =
                    excluded.created_at
            """,
            (
                market,
                model_version,
                parameter_name,
                float(parameter_value),
                validation_score,
                created_at
            )
        )

        db.commit()


def get_model_parameters(
    market: str,
    model_version: str | None = None
) -> dict[str, float]:

    with get_connection() as db:

        if model_version:

            rows = db.execute(
                """
                SELECT
                    parameter_name,
                    parameter_value
                FROM model_parameters
                WHERE
                    market = ?
                    AND model_version = ?
                """,
                (
                    market,
                    model_version
                )
            ).fetchall()

        else:

            rows = db.execute(
                """
                SELECT
                    parameter_name,
                    parameter_value
                FROM model_parameters
                WHERE
                    market = ?
                ORDER BY created_at DESC
                """,
                (
                    market,
                )
            ).fetchall()

    result = {}

    for row in rows:

        if row["parameter_name"] not in result:

            result[
                row["parameter_name"]
            ] = float(
                row["parameter_value"]
            )

    return result


# ============================================================
# SYNC RUNS
# ============================================================

def save_sync_run(
    market: str,
    started_at: str,
    finished_at: str,
    status: str,
    records_found: int = 0,
    records_added: int = 0,
    error: str | None = None
):

    with get_connection() as db:

        db.execute(
            """
            INSERT INTO sync_runs
            (
                market,
                started_at,
                finished_at,
                status,
                records_found,
                records_added,
                error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                started_at,
                finished_at,
                status,
                records_found,
                records_added,
                error
            )
        )

        db.commit()


def latest_sync(
    market: str
):

    with get_connection() as db:

        row = db.execute(
            """
            SELECT *
            FROM sync_runs
            WHERE market = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                market,
            )
        ).fetchone()

    return dict(row) if row else None


# ============================================================
# MARKET TIME SLOTS
# ============================================================

def save_market_time_slot(
    market: str,
    update_time: str,
    enabled: bool = True
):

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as db:

        db.execute(
            """
            INSERT INTO market_time_slots
            (
                market,
                update_time,
                enabled,
                created_at
            )
            VALUES (?, ?, ?, ?)

            ON CONFLICT(
                market,
                update_time
            )

            DO UPDATE SET
                enabled =
                    excluded.enabled
            """,
            (
                market,
                update_time,
                1 if enabled else 0,
                created_at
            )
        )

        db.commit()


def get_market_time_slots(
    market: str
) -> list[dict[str, Any]]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT
                id,
                market,
                update_time,
                enabled,
                created_at
            FROM market_time_slots
            WHERE market = ?
            ORDER BY update_time
            """,
            (
                market,
            )
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def delete_market_time_slot(
    market: str,
    update_time: str
):

    with get_connection() as db:

        db.execute(
            """
            DELETE FROM market_time_slots
            WHERE
                market = ?
                AND update_time = ?
            """,
            (
                market,
                update_time
            )
        )

        db.commit()


# ============================================================
# EXPORT JSON
# ============================================================

def export_json(
    market: str
) -> str:

    rows = get_result_rows(
        market
    )

    return json.dumps(
        rows,
        indent=2,
        ensure_ascii=False
    )


# ============================================================
# EXPORT CSV
# ============================================================

def export_csv(
    market: str
) -> str:

    rows = get_result_rows(
        market
    )

    output = io.StringIO()

    fieldnames = [
        "id",
        "market",
        "value",
        "source_url",
        "source_index",
        "fetched_at",
        "result_date",
        "update_time",
        "sequence",
        "note"
    ]

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames
    )

    writer.writeheader()

    writer.writerows(
        rows
    )

    return output.getvalue()


# ============================================================
# START DATABASE
# ============================================================

init_db()
