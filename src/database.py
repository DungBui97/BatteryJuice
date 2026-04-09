"""SQLite storage layer for BatteryJuice."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _expand(db_path: str) -> str:
    return str(Path(db_path).expanduser())


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(_expand(db_path))
    con.row_factory = sqlite3.Row
    return con


def init_db(db_path: str) -> None:
    Path(_expand(db_path)).parent.mkdir(parents=True, exist_ok=True)
    with _conn(db_path) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp           TEXT NOT NULL,
                cycle_count         INTEGER,
                max_capacity_mah    INTEGER,
                design_capacity_mah INTEGER,
                current_pct         INTEGER,
                power_draw_w        REAL,
                temperature_c       REAL,
                voltage_mv          INTEGER,
                is_charging         INTEGER,
                model               TEXT
            );

            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                filepath     TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON snapshots (timestamp);
        """)


def insert_snapshot(db_path: str, data: dict) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as con:
        con.execute(
            """INSERT INTO snapshots
               (timestamp, cycle_count, max_capacity_mah, design_capacity_mah,
                current_pct, power_draw_w, temperature_c, voltage_mv, is_charging, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts,
                data.get("cycle_count"),
                data.get("max_capacity_mah"),
                data.get("design_capacity_mah"),
                data.get("current_pct"),
                data.get("power_draw_w"),
                data.get("temperature_c"),
                data.get("voltage_mv"),
                1 if data.get("is_charging") else 0,
                data.get("model"),
            ),
        )


def get_snapshots(db_path: str, start: str, end: str) -> list[dict]:
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM snapshots WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start, end),
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_snapshots(db_path: str) -> list[dict]:
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM snapshots ORDER BY timestamp").fetchall()
    return [dict(r) for r in rows]


def get_latest_snapshot(db_path: str) -> dict | None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def log_report(db_path: str, filepath: str, period_start: str, period_end: str) -> None:
    with _conn(db_path) as con:
        con.execute(
            "INSERT INTO reports (generated_at, filepath, period_start, period_end) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), filepath, period_start, period_end),
        )


def get_reports(db_path: str, limit: int = 10) -> list[dict]:
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT * FROM reports ORDER BY generated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_last_report_time(db_path: str) -> str | None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT generated_at FROM reports ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
    return row["generated_at"] if row else None


def prune_old_snapshots(db_path: str, retention_days: int) -> int:
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = (cutoff - timedelta(days=retention_days)).isoformat()
    with _conn(db_path) as con:
        cur = con.execute("DELETE FROM snapshots WHERE timestamp < ?", (cutoff,))
        return cur.rowcount


def export_csv(db_path: str, output_path: str) -> int:
    rows = get_all_snapshots(db_path)
    if not rows:
        return 0
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
