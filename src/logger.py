import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"
DB_PATH = LOGS_DIR / "conversations.db"

_initialized = False
_init_lock = threading.Lock()
_pg_initialized = False
_pg_warned = False


def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    try:
        from dotenv import load_dotenv

        load_dotenv()
        return (os.getenv("DATABASE_URL") or "").strip()
    except Exception:
        return ""


def _pg_connect():
    """Return a Postgres connection when DATABASE_URL is configured and usable."""
    global _pg_warned
    url = _database_url()
    if not url:
        return None
    try:
        import psycopg
    except Exception:
        if not _pg_warned:
            print("[logger] DATABASE_URL is set but psycopg is not installed — using local SQLite.")
            _pg_warned = True
        return None
    try:
        return psycopg.connect(url, connect_timeout=6)
    except Exception as exc:
        if not _pg_warned:
            print(f"[logger] live database unreachable ({type(exc).__name__}) — using local SQLite.")
            _pg_warned = True
        return None


def _ensure_pg(conn) -> None:
    global _pg_initialized
    if _pg_initialized:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                client_id TEXT NOT NULL DEFAULT 'unknown',
                timestamp TEXT NOT NULL,
                query TEXT NOT NULL,
                response TEXT NOT NULL,
                escalated INTEGER NOT NULL DEFAULT 0,
                escalation_reason TEXT,
                context TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_client_id ON conversations(client_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp)"
        )
    conn.commit()
    _pg_initialized = True


def _ensure_sqlite():
    """Create/migrate the local schema once per process. Additive only."""
    global _initialized
    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return

        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(conversations)")
            existing_columns = [col[1] for col in cursor.fetchall()]

            if not existing_columns:
                conn.execute(
                    """
                    CREATE TABLE conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        client_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        query TEXT NOT NULL,
                        response TEXT NOT NULL,
                        escalated INTEGER NOT NULL DEFAULT 0,
                        escalation_reason TEXT,
                        context TEXT
                    )
                    """
                )
            elif "client_id" not in existing_columns:
                conn.execute(
                    "ALTER TABLE conversations ADD COLUMN client_id TEXT NOT NULL DEFAULT 'unknown'"
                )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_client_id ON conversations(client_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp)"
            )
            conn.commit()
        finally:
            conn.close()

        _initialized = True


def log_exchange(
    session_id: str,
    client_id: str,
    query: str,
    response: str,
    escalated: bool = False,
    escalation_reason: str = None,
    context: list[dict] = None,
):
    """Write one exchange. Never lets a logging failure break a customer chat."""
    timestamp = datetime.now(timezone.utc).isoformat()
    context_json = json.dumps(context) if context else None
    values = (
        session_id,
        client_id,
        timestamp,
        query,
        response,
        1 if escalated else 0,
        escalation_reason,
        context_json,
    )

    pg = _pg_connect()
    if pg is not None:
        try:
            _ensure_pg(pg)
            with pg.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations
                        (session_id, client_id, timestamp, query, response,
                         escalated, escalation_reason, context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    values,
                )
            pg.commit()
            return
        except Exception as exc:
            print(f"[logger] Postgres insert failed ({type(exc).__name__}) — falling back to SQLite.")
        finally:
            pg.close()

    try:
        _ensure_sqlite()
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(
                """
                INSERT INTO conversations
                    (session_id, client_id, timestamp, query, response,
                     escalated, escalation_reason, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[logger] local insert failed ({type(exc).__name__}).")
