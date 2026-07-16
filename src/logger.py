import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"
DB_PATH = LOGS_DIR / "conversations.db"


def _ensure_db():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))

    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(conversations)")
    existing_columns = [col[1] for col in cursor.fetchall()]

    if "client_id" not in existing_columns:
        conn.execute("DROP TABLE IF EXISTS conversations")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_client_id ON conversations(client_id)")
    conn.commit()
    conn.close()


def log_exchange(
    session_id: str,
    client_id: str,
    query: str,
    response: str,
    escalated: bool = False,
    escalation_reason: str = None,
    context: list[dict] = None,
):
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT INTO conversations (session_id, client_id, timestamp, query, response, escalated, escalation_reason, context)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            client_id,
            datetime.now(timezone.utc).isoformat(),
            query,
            response,
            1 if escalated else 0,
            escalation_reason,
            json.dumps(context) if context else None,
        ),
    )
    conn.commit()
    conn.close()
