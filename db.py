"""SQLite helpers for TrapGuard AI.

The project intentionally uses sqlite3 from the Python standard library so a
student can understand and debug the database without an ORM.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).with_name("trapguard.db")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection that returns rows like dictionaries."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and seed small demo data when the database is empty."""
    with get_conn() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS cameras(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                zone_tag TEXT NOT NULL CHECK(zone_tag IN ('corridor','road','village','waterhole')),
                protected INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sightings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                label TEXT NOT NULL,
                "group" TEXT NOT NULL CHECK("group" IN ('animal','human','vehicle','object')),
                confidence REAL NOT NULL,
                cx REAL NOT NULL,
                cy REAL NOT NULL,
                w REAL NOT NULL,
                h REAL NOT NULL,
                FOREIGN KEY(camera_id) REFERENCES cameras(id)
            );

            CREATE TABLE IF NOT EXISTS alerts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('info','warning','high','critical')),
                message TEXT NOT NULL,
                snapshot_path TEXT,
                status TEXT NOT NULL CHECK(status IN ('pending','verified_ranger','crime','escalated','scheduled_ranger','resolved')),
                resolved_by TEXT,
                resolved_at TEXT,
                FOREIGN KEY(camera_id) REFERENCES cameras(id)
            );

            CREATE TABLE IF NOT EXISTS patrol_schedule(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ranger_name TEXT NOT NULL,
                camera_id INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                FOREIGN KEY(camera_id) REFERENCES cameras(id)
            );

            CREATE TABLE IF NOT EXISTS rangers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                telegram_chat_id TEXT,
                pin TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'ranger'
            );
            """
        )

        if conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO cameras(name, lat, lng, zone_tag, protected) VALUES(?,?,?,?,?)",
                [
                    ("Cam-01 Waterhole North", 20.2460, 79.3400, "waterhole", 0),
                    ("Cam-02 Road Junction", 20.2505, 79.3470, "road", 0),
                    ("Cam-03 Village Edge", 20.2570, 79.3550, "village", 0),
                    ("Cam-04 Core Forest Trail", 20.2415, 79.3310, "corridor", 1),
                ],
            )

        if conn.execute("SELECT COUNT(*) FROM rangers").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO rangers(name, telegram_chat_id, pin, role) VALUES(?,?,?,?)",
                [
                    ("Ranger Asha", None, "1111", "ranger"),
                    ("Ranger Ravi", None, "2222", "ranger"),
                    ("Ranger Neel", None, "3333", "ranger"),
                    ("Admin", None, "9999", "admin"),
                ],
            )

        if conn.execute("SELECT COUNT(*) FROM patrol_schedule").fetchone()[0] == 0:
            now = datetime.utcnow()
            rows = [
                ("Ranger Asha", 4, (now - timedelta(hours=1)).isoformat(), (now + timedelta(hours=1)).isoformat()),
                ("Ranger Ravi", 2, (now + timedelta(hours=2)).isoformat(), (now + timedelta(hours=4)).isoformat()),
                ("Ranger Neel", 3, (now + timedelta(hours=5)).isoformat(), (now + timedelta(hours=7)).isoformat()),
            ]
            conn.executemany(
                "INSERT INTO patrol_schedule(ranger_name,camera_id,start_time,end_time) VALUES(?,?,?,?)",
                rows,
            )


def rows_to_dicts(rows):
    """Convert sqlite Row objects into normal dicts for JSON responses."""
    return [dict(row) for row in rows]
