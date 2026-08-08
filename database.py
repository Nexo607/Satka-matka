from __future__ import annotations

import csv
import io
import json
import sqlite3
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


def init_db():

    with get_connection() as db:

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

        db.commit()


def insert_results(
    market: str,
    values: list[str],
    source_url: str,
    fetched_at: str
) -> int:

    added = 0

    with get_connection() as db:

        for index, value in enumerate(values):

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO results
                (
                    market,
                    value,
                    source_url,
                    source_index,
                    fetched_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    market,
                    value,
                    source_url,
                    index,
                    fetched_at
                )
            )

            if cursor.rowcount:
                added += 1

        db.commit()

    return added


def get_values(
    market: str
) -> list[str]:

    with get_connection() as db:

        rows = db.execute(
            """
            SELECT value
            FROM results
            WHERE market = ?
            ORDER BY id ASC
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
                fetched_at
            FROM results
            WHERE market = ?
            ORDER BY id ASC
            """,
            (market,)
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


def export_json(
    market: str
) -> str:

    rows = get_result_rows(market)

    return json.dumps(
        rows,
        indent=2
    )


def export_csv(
    market: str
) -> str:

    rows = get_result_rows(market)

    output = io.StringIO()

    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "market",
            "value",
            "source_url",
            "source_index",
            "fetched_at"
        ]
    )

    writer.writeheader()

    writer.writerows(rows)

    return output.getvalue()
