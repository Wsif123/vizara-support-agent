"""Local admin database: connection helpers and idempotent migrations."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ADMIN_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = ADMIN_DIR / "migrations"

# Tests can point these elsewhere; normal runs always use admin/admin.db.
DB_PATH = Path(os.getenv("VIZARA_ADMIN_DB", str(ADMIN_DIR / "admin.db")))
BACKUP_DIR = Path(os.getenv("VIZARA_ADMIN_BACKUPS", str(ADMIN_DIR / "backups")))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create the schema and seed the default plan. Safe to run every boot."""
    with db() as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))

        existing = conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"]
        if not existing:
            stamp = now_iso()
            conn.execute(
                """
                INSERT INTO plans (name, amount_cents, currency, interval, setup_fee_cents, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                ("Chatbot Starter", 9000, "USD", "month", 15000),
            )
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('seeded_at', ?)",
                (stamp,),
            )
        conn.commit()


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def audit(
    conn: sqlite3.Connection,
    action: str,
    target: str | None = None,
    details: str | None = None,
    actor: str = "operator",
    ip: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, target, ip, details) VALUES (?, ?, ?, ?, ?, ?)",
        (now_iso(), actor, action, target, ip, details),
    )
