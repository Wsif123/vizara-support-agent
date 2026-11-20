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


def period_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _pg_connect():
    """Return a Postgres connection when DATABASE_URL is configured and usable."""
    url = _database_url()
    if not url:
        return None
    import psycopg

    return psycopg.connect(url, connect_timeout=6)


def init_db() -> None:
    url = _database_url()
    if not url:
        print("[logger] no DATABASE_URL, using local SQLite")
        return

    try:
        import psycopg  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "DATABASE_URL is set but psycopg is not installed"
        ) from exc

    try:
        conn = _pg_connect()
    except Exception as exc:
        raise RuntimeError(
            f"DATABASE_URL is set but unreachable: {type(exc).__name__}"
        ) from exc

    try:
        _ensure_pg(conn)
    finally:
        conn.close()
    print("[logger] postgres ready")


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
    _ensure_usage(conn)
    conn.commit()
    _pg_initialized = True


def _ensure_usage(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS client_usage (
                client_id TEXT NOT NULL,
                period TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (client_id, period)
            )
            """
        )
    finally:
        cur.close()
    conn.commit()


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
            _ensure_usage(conn)
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

    if _database_url():
        pg = None
        try:
            pg = _pg_connect()
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
            print(
                f"[logger] postgres insert failed ({type(exc).__name__}) "
                "— falling back to SQLite"
            )
        finally:
            if pg is not None:
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


def bump_usage(client_id: str) -> None:
    """Increment the current month's conversation count. Never raises."""
    period = period_key()

    if _database_url():
        pg = None
        try:
            pg = _pg_connect()
            _ensure_usage(pg)
            with pg.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO client_usage (client_id, period, count)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (client_id, period)
                    DO UPDATE SET count = client_usage.count + 1
                    """,
                    (client_id, period),
                )
            pg.commit()
            return
        except Exception as exc:
            print(
                f"[logger] postgres usage bump failed ({type(exc).__name__}) "
                "— falling back to SQLite"
            )
        finally:
            if pg is not None:
                pg.close()

    try:
        _ensure_sqlite()
        conn = sqlite3.connect(str(DB_PATH))
        try:
            _ensure_usage(conn)
            conn.execute(
                """
                INSERT INTO client_usage (client_id, period, count)
                VALUES (?, ?, 1)
                ON CONFLICT (client_id, period)
                DO UPDATE SET count = client_usage.count + 1
                """,
                (client_id, period),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[logger] local usage bump failed ({type(exc).__name__}).")


def usage_count(client_id: str) -> int:
    """Return the current month's conversation count, or 0 on failure."""
    period = period_key()

    if _database_url():
        pg = None
        try:
            pg = _pg_connect()
            _ensure_usage(pg)
            with pg.cursor() as cur:
                cur.execute(
                    """
                    SELECT count FROM client_usage
                    WHERE client_id = %s AND period = %s
                    """,
                    (client_id, period),
                )
                row = cur.fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            print(
                f"[logger] postgres usage read failed ({type(exc).__name__}) "
                "— falling back to SQLite"
            )
        finally:
            if pg is not None:
                pg.close()

    try:
        _ensure_sqlite()
        conn = sqlite3.connect(str(DB_PATH))
        try:
            _ensure_usage(conn)
            row = conn.execute(
                """
                SELECT count FROM client_usage
                WHERE client_id = ? AND period = ?
                """,
                (client_id, period),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception as exc:
        print(f"[logger] usage read failed ({type(exc).__name__}).")
        return 0
