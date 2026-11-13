"""Read-only conversation analytics for the console.

Uses local SQLite by default and Neon/Postgres when DATABASE_URL is set. The
dashboard never writes to the bot's conversation log.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = REPO_ROOT / "logs" / "conversations.db"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def _pg_connect(timeout: int = 4):
    url = database_url()
    if not url:
        return None
    try:
        import psycopg

        return psycopg.connect(url, connect_timeout=timeout)
    except Exception:
        return None


def source_status() -> dict:
    url = database_url()
    if not url:
        return {
            "mode": "local",
            "ok": True,
            "label": "Local SQLite",
            "detail": str(SQLITE_PATH),
        }
    try:
        import psycopg  # noqa: F401
    except Exception:
        return {
            "mode": "postgres",
            "ok": False,
            "label": "Live Postgres",
            "detail": "DATABASE_URL is set, but psycopg is not installed. Run: pip install psycopg[binary]",
        }
    conn = _pg_connect()
    if not conn:
        return {
            "mode": "postgres",
            "ok": False,
            "label": "Live Postgres",
            "detail": "DATABASE_URL is set, but the database is unreachable.",
        }
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM conversations")
            count = cur.fetchone()[0]
        return {
            "mode": "postgres",
            "ok": True,
            "label": "Live Postgres",
            "detail": f"{count:,} conversations · Neon/Render data",
        }
    except Exception as exc:
        return {
            "mode": "postgres",
            "ok": False,
            "label": "Live Postgres",
            "detail": f"Connected, but conversations table is unavailable ({type(exc).__name__}).",
        }
    finally:
        conn.close()


def _connect_readonly():
    if database_url():
        return _pg_connect()
    if not SQLITE_PATH.exists():
        return None
    try:
        return sqlite3.connect(
            f"file:{SQLITE_PATH.as_posix()}?mode=ro", uri=True, timeout=5
        )
    except sqlite3.Error:
        return None


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = _connect_readonly()
    if not conn:
        return []
    try:
        if database_url():
            with conn.cursor() as cur:
                cur.execute(sql.replace("?", "%s"), params)
                columns = [desc[0] for desc in cur.description]
                return [dict(zip(columns, row)) for row in cur.fetchall()]
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _scalar(sql: str, params: tuple = (), default=0):
    rows = _rows(sql, params)
    if not rows:
        return default
    return next(iter(rows[0].values()))


def conversation_rows(
    days: int = 7,
    client_id: str | None = None,
    escalated: bool | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    clauses = ["timestamp >= ?"]
    params: list = [_iso(datetime.now(timezone.utc) - timedelta(days=max(1, days)))]
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    if escalated is True:
        clauses.append("escalated = ?")
        params.append(True)
    elif escalated is False:
        clauses.append("escalated = ?")
        params.append(False)
    if search:
        clauses.append("(LOWER(query) LIKE ? OR LOWER(response) LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, needle])
    where = " AND ".join(clauses)

    total = _scalar(f"SELECT COUNT(*) AS n FROM conversations WHERE {where}", tuple(params))
    rows = _rows(
        f"""
        SELECT id, session_id, client_id, timestamp, query, response, escalated,
               escalation_reason
        FROM conversations
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {limit} OFFSET {offset}
        """,
        tuple(params),
    )
    return rows, int(total or 0)


def conversation(conversation_id: int) -> dict | None:
    rows = _rows(
        """
        SELECT id, session_id, client_id, timestamp, query, response, escalated,
               escalation_reason, context
        FROM conversations WHERE id = ?
        """,
        (int(conversation_id),),
    )
    return rows[0] if rows else None


def stats(days: int = 7) -> dict:
    days = max(1, min(365, int(days)))
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    rows = _rows(
        """
        SELECT id, session_id, client_id, timestamp, query, escalated
        FROM conversations WHERE timestamp >= ?
        """,
        (_iso(start),),
    )

    total = len(rows)
    escalated = sum(1 for row in rows if row["escalated"])
    sessions = len({row["session_id"] for row in rows})
    by_client: Counter = Counter()
    esc_by_client: Counter = Counter()
    last_active: dict[str, str] = {}
    questions: Counter = Counter()
    question_label: dict[str, str] = {}
    daily: dict[str, int] = {}
    daily_esc: dict[str, int] = {}

    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        daily[day] = 0
        daily_esc[day] = 0

    for row in rows:
        client = row["client_id"] or "unknown"
        by_client[client] += 1
        if row["escalated"]:
            esc_by_client[client] += 1
        stamp = (row["timestamp"] or "")[:10]
        if stamp and stamp > last_active.get(client, ""):
            last_active[client] = stamp
        query = (row["query"] or "").strip()
        normalized = " ".join(query.lower().split())[:160]
        if normalized:
            questions[normalized] += 1
            question_label.setdefault(normalized, query[:160])
        if stamp in daily:
            daily[stamp] += 1
            if row["escalated"]:
                daily_esc[stamp] += 1

    return {
        "days": days,
        "messages": total,
        "escalated": escalated,
        "escalation_rate": round(escalated / total * 100, 1) if total else 0.0,
        "sessions": sessions,
        "by_client": dict(by_client),
        "escalated_by_client": dict(esc_by_client),
        "last_active": last_active,
        "daily": [{"date": key, "count": daily[key], "escalated": daily_esc[key]} for key in daily],
        "top_questions": [
            {"text": question_label[key], "count": count}
            for key, count in questions.most_common(8)
        ],
    }


def client_usage(days: int = 7) -> dict[str, dict]:
    data = stats(days)
    result: dict[str, dict] = {}
    for client_id, count in data["by_client"].items():
        result[client_id] = {
            "messages": count,
            "escalated": data["escalated_by_client"].get(client_id, 0),
            "last_active": data["last_active"].get(client_id),
        }
    return result


def recent_escalations(limit: int = 6) -> list[dict]:
    limit = max(1, min(50, int(limit)))
    return _rows(
        f"""
        SELECT id, client_id, timestamp, query, escalation_reason
        FROM conversations
        WHERE escalated = 1
        ORDER BY timestamp DESC
        LIMIT {limit}
        """
    )


def escalated_ids(limit: int = 1000) -> list[int]:
    rows = _rows(
        f"""
        SELECT id FROM conversations
        WHERE escalated = 1
        ORDER BY timestamp DESC
        LIMIT {max(1, min(5000, int(limit)))}
        """
    )
    return [row["id"] for row in rows]


def open_escalation_count(admin_conn) -> int:
    """Escalated conversations that do not have a resolution note yet."""
    ids = escalated_ids()
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    resolved = admin_conn.execute(
        f"SELECT conversation_id FROM conversation_notes WHERE conversation_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    resolved_ids = {row["conversation_id"] for row in resolved}
    return sum(1 for item in ids if item not in resolved_ids)


def conversation_count() -> int:
    if database_url():
        return int(_scalar("SELECT COUNT(*) AS n FROM conversations", default=0))
    if not SQLITE_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{SQLITE_PATH.as_posix()}?mode=ro", uri=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def export_rows(days: int = 365, client_id: str | None = None) -> list[dict]:
    # A larger hard cap than the interactive table, still bounded.
    clauses = ["timestamp >= ?"]
    params: list = [_iso(datetime.now(timezone.utc) - timedelta(days=max(1, days)))]
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    return _rows(
        f"""
        SELECT id, timestamp, client_id, session_id, query, response, escalated,
               escalation_reason
        FROM conversations WHERE {' AND '.join(clauses)}
        ORDER BY timestamp DESC LIMIT 20000
        """,
        tuple(params),
    )


def system_files() -> list[dict]:
    files = [
        ("Conversation DB", SQLITE_PATH),
        ("Admin DB", REPO_ROOT / "admin" / "admin.db"),
        ("Chroma index", REPO_ROOT / "chroma_db" / "chroma.sqlite3"),
    ]
    result = []
    for label, path in files:
        size = path.stat().st_size if path.exists() else 0
        result.append(
            {
                "label": label,
                "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
                "size_kb": round(size / 1024, 1),
                "exists": path.exists(),
            }
        )
    return result
