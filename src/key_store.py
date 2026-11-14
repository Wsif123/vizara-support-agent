"""Shared Groq key store used by both the bot and the operator console.

Resolution order:
  1. Active, non-cooling keys in admin/admin.db (unless KEY_SOURCE=env).
  2. GROQ_API_KEYS, then SUPPORT_AGENT_GROQ_API_KEY from the environment.

The full key is decrypted in memory only. It is never logged or rendered.
If anything about the admin DB is unavailable (for example on Render), this
module silently falls back to environment keys so the public bot keeps working.
"""

from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_DB = Path(os.getenv("VIZARA_ADMIN_DB", str(REPO_ROOT / "admin" / "admin.db")))
SECRET_PATH = Path(
    os.getenv("VIZARA_SECRET_KEY_PATH", str(REPO_ROOT / "admin" / ".secret.key"))
)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_MODEL = "openai/gpt-oss-20b"

_lock = threading.Lock()
_rr_pos = 0
_secret_cache: bytes | None = None
_env_cooldowns: dict[str, float] = {}  # marker -> unix deadline


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def _admin_conn() -> sqlite3.Connection | None:
    if not ADMIN_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(ADMIN_DB), timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000")
        return conn
    except sqlite3.Error:
        return None


def _has_db_keys(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM api_keys WHERE provider = 'groq'"
        ).fetchone()
        return bool(row and row["n"])
    except sqlite3.Error:
        return False


def _env_keys() -> list[dict]:
    _load_dotenv()
    raw = os.getenv("GROQ_API_KEYS", "").strip()
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if not values:
        single = os.getenv("SUPPORT_AGENT_GROQ_API_KEY", "").strip()
        if single:
            values = [single]
    return [
        {
            "id": None,
            "label": f"Groq - env {i + 1}",
            "hint": value[-4:] if len(value) >= 4 else "••••",
            "secret": value,
            "weight": 1,
            "source": "env",
            "status": "active",
        }
        for i, value in enumerate(values)
    ]


def _env_marker(key: dict) -> str:
    return f"{key.get('label')}|{(key.get('secret') or '')[-6:]}"


def _prune_env_cooldowns(now: float | None = None) -> None:
    current = time.time() if now is None else now
    for marker, until in list(_env_cooldowns.items()):
        if until <= current:
            del _env_cooldowns[marker]


def _env_cooling(key: dict) -> bool:
    if key.get("id") is not None:
        return False
    _prune_env_cooldowns()
    until = _env_cooldowns.get(_env_marker(key))
    return bool(until and until > time.time())


def _load_secret() -> bytes | None:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    if not SECRET_PATH.exists():
        return None
    try:
        value = SECRET_PATH.read_bytes().strip()
        _secret_cache = value
        return value
    except OSError:
        return None


def ensure_secret() -> bytes:
    """Create the Fernet master key if it does not exist. Dashboard only."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    existing = _load_secret()
    if existing:
        return existing

    from cryptography.fernet import Fernet

    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    value = Fernet.generate_key()
    SECRET_PATH.write_bytes(value)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass
    _secret_cache = value
    return value


def encrypt_secret(plaintext: str) -> str:
    from cryptography.fernet import Fernet

    return Fernet(ensure_secret()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    key = _load_secret()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception:
        return None


def get_groq_model() -> str:
    _load_dotenv()
    fallback = os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    conn = _admin_conn()
    if not conn:
        return fallback
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'groq_model'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return fallback


def _cooldown_active(value: str | None) -> bool:
    if not value:
        return False
    return value > now_iso()


def _db_keys() -> tuple[bool, list[dict]]:
    conn = _admin_conn()
    if not conn:
        return False, []
    try:
        if not _has_db_keys(conn):
            return False, []
        rows = conn.execute(
            """
            SELECT id, label, hint, ciphertext, status, weight, cooldown_until
            FROM api_keys
            WHERE provider = 'groq'
            ORDER BY id
            """
        ).fetchall()
        keys: list[dict] = []
        for row in rows:
            status = row["status"]
            if status in {"disabled", "invalid"}:
                continue
            if _cooldown_active(row["cooldown_until"]):
                continue
            secret = decrypt_secret(row["ciphertext"])
            if not secret:
                continue
            keys.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "hint": row["hint"],
                    "secret": secret,
                    "weight": max(1, min(10, int(row["weight"] or 1))),
                    "source": "db",
                    "status": status,
                }
            )
        return True, keys
    except sqlite3.Error:
        return False, []
    finally:
        conn.close()


def read_env_key_values() -> list[str]:
    return [item["secret"] for item in _env_keys()]


def all_keys() -> list[dict]:
    if os.getenv("KEY_SOURCE", "").strip().lower() == "env":
        return _env_keys()
    has_db, keys = _db_keys()
    if has_db:
        return keys
    return _env_keys()


def select_keys() -> list[dict]:
    """Return the pool in weighted round-robin order, one entry per key."""
    global _rr_pos
    _prune_env_cooldowns()
    keys = all_keys()
    if not keys:
        return []
    expanded: list[dict] = []
    for key in keys:
        expanded.extend([key] * key["weight"])
    with _lock:
        start = _rr_pos % len(expanded)
        _rr_pos = (_rr_pos + 1) % max(1, len(expanded))
        ordered = expanded[start:] + expanded[:start]

    seen: set[object] = set()
    unique: list[dict] = []
    for key in ordered:
        marker = key["id"] if key["id"] is not None else key["secret"]
        if marker not in seen:
            seen.add(marker)
            unique.append(key)
    return [key for key in unique if not _env_cooling(key)]


def _cooldown_remaining(value: str | None, now: float) -> float | None:
    if not value:
        return None
    try:
        until = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until.timestamp() - now


def shortest_cooldown() -> float | None:
    """Return seconds until the nearest active key can be used again.

    Env keys have no database row, so their cooldowns live in memory. DB keys
    keep their cooldown in api_keys.cooldown_until. Only the active source is
    considered: DB keys when they exist (unless KEY_SOURCE=env), otherwise the
    env keys. Return None only when there are no active keys configured.
    """
    now = time.time()
    _prune_env_cooldowns(now)

    rows: list[sqlite3.Row] = []
    conn = _admin_conn()
    if conn:
        try:
            rows = conn.execute(
                """
                SELECT status, cooldown_until
                FROM api_keys
                WHERE provider = 'groq'
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()

    use_env = os.getenv("KEY_SOURCE", "").strip().lower() == "env" or not rows
    if use_env:
        env_keys = _env_keys()
        if not env_keys:
            return None
        waits = [
            until - now
            for key in env_keys
            if (until := _env_cooldowns.get(_env_marker(key))) and until > now
        ]
        return max(0.0, min(waits)) if waits else 0.0

    active_rows = [
        row for row in rows if row["status"] not in {"disabled", "invalid"}
    ]
    if not active_rows:
        return None

    waits = []
    for row in active_rows:
        remaining = _cooldown_remaining(row["cooldown_until"], now)
        if remaining is not None and remaining > 0:
            waits.append(remaining)
    return max(0.0, min(waits)) if waits else 0.0


def _usage_conn() -> sqlite3.Connection | None:
    return _admin_conn()


def record_result(
    key: dict,
    status: str,
    http_status: int | None = None,
    usage: dict | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
    model: str | None = None,
) -> None:
    key_id = key.get("id")
    if key_id is None:
        return
    usage = usage or {}
    conn = _usage_conn()
    if not conn:
        return
    stamp = now_iso()
    try:
        conn.execute(
            """
            INSERT INTO key_usage
                (key_id, ts, model, status, http_status, prompt_tokens,
                 completion_tokens, total_tokens, latency_ms, error_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_id,
                stamp,
                model,
                status,
                http_status,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                int(usage.get("total_tokens") or 0),
                latency_ms,
                (error_code or "")[:80] or None,
            ),
        )
        if status == "success":
            conn.execute(
                """
                UPDATE api_keys
                SET last_used_at = ?, last_checked_at = ?, status = 'active',
                    cooldown_until = NULL, updated_at = ?
                WHERE id = ? AND status != 'disabled'
                """,
                (stamp, stamp, stamp, key_id),
            )
        else:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, key_id),
            )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def cool_down(key: dict, seconds: int = 60) -> None:
    _prune_env_cooldowns()
    key_id = key.get("id")
    if key_id is None:
        _env_cooldowns[_env_marker(key)] = time.time() + seconds
        return
    until = (
        datetime.now(timezone.utc) + timedelta(seconds=max(5, min(3600, seconds)))
    ).isoformat(timespec="seconds")
    _set_status(key_id, "cooldown", until)


def invalidate(key: dict) -> None:
    key_id = key.get("id")
    if key_id is None:
        return
    _set_status(key_id, "invalid", None)


def _set_status(key_id: int, status: str, cooldown_until: str | None) -> None:
    conn = _usage_conn()
    if not conn:
        return
    try:
        conn.execute(
            """
            UPDATE api_keys
            SET status = ?, cooldown_until = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, cooldown_until, now_iso(), key_id),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def retry_after_seconds(resp: httpx.Response, default: int = 60) -> int:
    value = resp.headers.get("retry-after")
    if not value:
        return default
    try:
        return max(5, min(3600, int(float(value))))
    except (TypeError, ValueError):
        return default


def test_key(secret: str, model: str | None = None) -> dict:
    """Auth-check a key and report whether the configured model is present."""
    model = model or get_groq_model()
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                GROQ_MODELS_URL,
                headers={"Authorization": f"Bearer {secret}"},
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return {
                "ok": False,
                "http_status": resp.status_code,
                "latency_ms": elapsed,
                "message": "Groq rejected this key." if resp.status_code in (401, 403) else f"Groq returned HTTP {resp.status_code}.",
                "models": [],
            }
        data = resp.json()
        models = [item.get("id", "") for item in data.get("data", []) if item.get("id")]
        if model not in models:
            return {
                "ok": False,
                "http_status": 200,
                "latency_ms": elapsed,
                "message": f"Key valid, but {model} is not in this key's model list.",
                "models": models,
            }
        return {
            "ok": True,
            "http_status": 200,
            "latency_ms": elapsed,
            "message": f"Key valid · {model} available",
            "models": models,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "http_status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "message": f"Network error: {type(exc).__name__}",
            "models": [],
        }


def random_delay() -> float:
    """Small jitter for future retry logic (kept side-effect free)."""
    return round(random.uniform(0.05, 0.2), 3)
