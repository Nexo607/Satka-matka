from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "nexo.db"


def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    return connection


def _column_exists(db, table: str, column: str) -> bool:
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
    if not _column_exists(db, table, column):
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():

    with get_connection() as db:

        # ---------------------------------------------------------
        # EXISTING V5 TABLE
        # ---------------------------------------------------------

        db.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                value TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_index INTEGER,
                fetched_at TEXT NOT NULL,
                UNIQUE(market, value, source_index)
            )
        """)

        # ---------------------------------------------------------
        # V6 DATABASE MIGRATION
        #
        # These columns are added without destroying existing data.
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # V6 TIME-SLOT INDEX
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # EXISTING SYNC TABLE
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # V6 MARKET TIME SLOT CONFIGURATION
        # ---------------------------------------------------------

        db.execute("""
            CREATE TABLE IF NOT EXISTS market_time_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                update_time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(market, update_time)
            )
        """)

        db.commit()

        # ---------------------------------------------------------
        # MIGRATE OLD RECORDS
        #
        # Existing V5 records receive their date/time from fetched_at
        # whenever possible.
        # ---------------------------------------------------------

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

                    # Keep the record valid even if the old timestamp
                    # cannot be parsed.
                    if not result_date:
                        result_date = None

                    if not update_time:
                        update_time = None

            db.execute(
                """
                UPDATE results
                SET
                    result_date = ?,
                    update_time = ?,
                    sequence = COALESCE(sequence, 1)
                WHERE id = ?
                """,
                (
                    result_date,
                    update_time,
                    row["id"]
                )
            )

        db.commit()


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

    # -------------------------------------------------------------
    # Automatically derive date/time from fetched_at when the
    # caller does not explicitly provide them.
    # -------------------------------------------------------------

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

            sequence = sequence_start + offset

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

        db.commit()

    return added


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
        fetched_at = datetime.utcnow().isoformat()

    with get_connection() as db:

        # ---------------------------------------------------------
        # V6 duplicate protection.
        #
        # Same market + date + time + sequence + value
        # will not be inserted twice.
        # ---------------------------------------------------------

        existing = db.execute(
            """
            SELECT id
            FROM results
            WHERE
                market = ?
                AND result_date = ?
                AND update_time = ?
                AND sequence = ?
                AND value = ?
            LIMIT 1
            """,
            (
                market,
                result_date,
                update_time,
                sequence,
                value
            )
        ).fetchone()

        if existing:
            return 0

        db.execute(
            """
            INSERT INTO results
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

        db.commit()

    return 1


def get_values(
    market: str
) -> list[str]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT value
            FROM results
            WHERE market = ?
            ORDER BY
                COALESCE(result_date, ''),
                COALESCE(update_time, ''),
                sequence,
                id
            """,
            (market,)
        ).fetchall()

    return [
        row["value"]
        for row in rows
    ]


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
            FROM results
            WHERE market = ?
            ORDER BY
                COALESCE(result_date, ''),
                COALESCE(update_time, ''),
                sequence,
                id
            """,
            (market,)
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


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
        FROM results
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


def get_available_time_slots(
    market: str
) -> list[str]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT DISTINCT update_time
            FROM results
            WHERE
                market = ?
                AND update_time IS NOT NULL
                AND update_time != ''
            ORDER BY update_time
            """,
            (market,)
        ).fetchall()

    return [
        row["update_time"]
        for row in rows
    ]


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
            FROM results
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
            (market,)
        ).fetchone()

    return dict(row) if row else None


def save_market_time_slot(
    market: str,
    update_time: str,
    enabled: bool = True
):

    created_at = datetime.utcnow().isoformat()

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
            ON CONFLICT(market, update_time)
            DO UPDATE SET
                enabled = excluded.enabled
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
            (market,)
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

    writer.writerows(rows)

    return output.getvalue()
