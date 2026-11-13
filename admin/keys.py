"""Dashboard-facing key management on top of src.key_store."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src import key_store

from admin.db import now_iso


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def import_env_keys(conn: sqlite3.Connection) -> int:
    """One-time import of the keys already present in .env.

    This gives the operator the two existing keys in the UI without pasting
    them again. Only Groq keys are imported.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'env_keys_imported'"
    ).fetchone()
    if row:
        return 0
    count = conn.execute("SELECT COUNT(*) AS n FROM api_keys").fetchone()["n"]
    if count:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('env_keys_imported', ?)",
            (now_iso(),),
        )
        conn.commit()
        return 0

    values = key_store.read_env_key_values()
    labels = ["Groq - main", "Groq - backup"]
    imported = 0
    for index, secret in enumerate(values):
        if not secret:
            continue
        label = labels[index] if index < len(labels) else f"Groq - key {index + 1}"
        hint = secret[-4:] if len(secret) >= 4 else "••••"
        conn.execute(
            """
            INSERT INTO api_keys
                (provider, label, ciphertext, hint, status, weight, created_at, updated_at)
            VALUES ('groq', ?, ?, ?, 'active', 1, ?, ?)
            """,
            (
                label,
                key_store.encrypt_secret(secret),
                hint,
                now_iso(),
                now_iso(),
            ),
        )
        imported += 1
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('env_keys_imported', ?)",
        (now_iso(),),
    )
    conn.commit()
    return imported


def _usage_for_key(conn: sqlite3.Connection, key_id: int) -> dict:
    day_start = (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )
    minute_start = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(
        timespec="seconds"
    )
    row = conn.execute(
        """
        SELECT COUNT(*) AS requests,
               COALESCE(SUM(CASE WHEN status = '429' THEN 1 ELSE 0 END), 0) AS r429,
               COALESCE(SUM(total_tokens), 0) AS tokens
        FROM key_usage
        WHERE key_id = ? AND ts >= ?
        """,
        (key_id, day_start),
    ).fetchone()
    recent = conn.execute(
        """
        SELECT COALESCE(SUM(total_tokens), 0) AS tokens
        FROM key_usage
        WHERE key_id = ? AND ts >= ?
        """,
        (key_id, minute_start),
    ).fetchone()
    return {
        "requests_today": row["requests"],
        "r429": row["r429"],
        "tokens_today": row["tokens"],
        "tokens_last_min": recent["tokens"],
    }


def list_keys(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM api_keys WHERE provider = 'groq' ORDER BY id"
    ).fetchall()
    keys: list[dict] = []
    total_requests = 0
    for row in rows:
        usage = _usage_for_key(conn, row["id"])
        total_requests += usage["requests_today"]
        keys.append(
            {
                "id": row["id"],
                "label": row["label"],
                "hint": row["hint"],
                "status": row["status"],
                "weight": row["weight"],
                "cooldown_until": row["cooldown_until"],
                "last_checked_at": row["last_checked_at"],
                "last_used_at": row["last_used_at"],
                "created_at": row["created_at"],
                **usage,
            }
        )

    now = datetime.now(timezone.utc)
    for key in keys:
        cooldown = _parse(key["cooldown_until"])
        key["cooling_seconds"] = (
            max(0, int((cooldown - now).total_seconds()))
            if cooldown and cooldown > now
            else 0
        )
        key["display_status"] = key["status"]
        if key["status"] == "cooldown" and key["cooling_seconds"] == 0:
            key["display_status"] = "active"
        if key["display_status"] == "active" and key["cooling_seconds"]:
            key["display_status"] = "cooldown"
        key["tpm_pct"] = min(100, round(key["tokens_last_min"] / 8000 * 100))
        key["daily_pct"] = min(100, round(key["requests_today"] / 1000 * 100))
        key["load_pct"] = (
            round(key["requests_today"] / total_requests * 100)
            if total_requests
            else 0
        )
        key["masked"] = f"gsk_…{key['hint']}"
    return keys


def summary(conn: sqlite3.Connection) -> dict:
    keys = list_keys(conn)
    active = sum(1 for k in keys if k["display_status"] == "active")
    issues = sum(1 for k in keys if k["display_status"] in {"invalid", "cooldown"})
    return {
        "key_list": keys,
        "active": active,
        "issues": issues,
        "total": len(keys),
        "requests_today": sum(k["requests_today"] for k in keys),
        "tokens_today": sum(k["tokens_today"] for k in keys),
        "r429": sum(k["r429"] for k in keys),
    }


def _duplicate(conn: sqlite3.Connection, secret: str) -> bool:
    rows = conn.execute(
        "SELECT ciphertext FROM api_keys WHERE provider = 'groq'"
    ).fetchall()
    for row in rows:
        if key_store.decrypt_secret(row["ciphertext"]) == secret:
            return True
    return False


def add_key(
    conn: sqlite3.Connection, label: str, secret: str, weight: int
) -> dict:
    label = (label or "").strip()
    secret = (secret or "").strip()
    if not label:
        raise ValueError("Give the key a label.")
    if len(label) > 60:
        raise ValueError("Label is too long (60 characters max).")
    if not secret.startswith("gsk_") or len(secret) < 20:
        raise ValueError("That does not look like a Groq key (expected gsk_…).")
    if _duplicate(conn, secret):
        raise ValueError("That key is already in the pool.")
    weight = max(1, min(10, int(weight or 1)))

    result = key_store.test_key(secret)
    if not result["ok"] and not (result.get("http_status") == 200):
        raise ValueError(result["message"])

    hint = secret[-4:]
    cur = conn.execute(
        """
        INSERT INTO api_keys
            (provider, label, ciphertext, hint, status, weight, last_checked_at,
             created_at, updated_at)
        VALUES ('groq', ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            label,
            key_store.encrypt_secret(secret),
            hint,
            weight,
            now_iso(),
            now_iso(),
            now_iso(),
        ),
    )
    conn.commit()
    return {"id": cur.lastrowid, "message": result["message"]}


def test_key(conn: sqlite3.Connection, key_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM api_keys WHERE id = ? AND provider = 'groq'", (key_id,)
    ).fetchone()
    if not row:
        raise ValueError("Key not found.")
    secret = key_store.decrypt_secret(row["ciphertext"])
    if not secret:
        conn.execute(
            "UPDATE api_keys SET status = 'invalid', last_checked_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), key_id),
        )
        conn.commit()
        return {
            "ok": False,
            "message": "Stored key could not be decrypted. Re-add it.",
            "latency_ms": 0,
        }

    result = key_store.test_key(secret)
    key = {"id": key_id, "label": row["label"]}
    if result["ok"]:
        key_store.record_result(
            key,
            "success",
            http_status=200,
            latency_ms=result["latency_ms"],
            model=key_store.get_groq_model(),
        )
    elif result.get("http_status") in (401, 403):
        key_store.record_result(
            key,
            "invalid",
            http_status=result["http_status"],
            latency_ms=result["latency_ms"],
            error_code="auth_failed",
        )
        key_store.invalidate(key)
    else:
        conn.execute(
            "UPDATE api_keys SET last_checked_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), key_id),
        )
        conn.commit()
    return result


def update_weight(conn: sqlite3.Connection, key_id: int, weight: int) -> None:
    weight = max(1, min(10, int(weight)))
    conn.execute(
        "UPDATE api_keys SET weight = ?, updated_at = ? WHERE id = ? AND provider = 'groq'",
        (weight, now_iso(), key_id),
    )
    conn.commit()


def toggle_key(conn: sqlite3.Connection, key_id: int) -> str:
    row = conn.execute(
        "SELECT status FROM api_keys WHERE id = ? AND provider = 'groq'", (key_id,)
    ).fetchone()
    if not row:
        raise ValueError("Key not found.")
    new_status = "disabled" if row["status"] != "disabled" else "active"
    conn.execute(
        """
        UPDATE api_keys
        SET status = ?, cooldown_until = NULL, updated_at = ?
        WHERE id = ?
        """,
        (new_status, now_iso(), key_id),
    )
    conn.commit()
    return new_status


def delete_key(conn: sqlite3.Connection, key_id: int) -> None:
    conn.execute("DELETE FROM api_keys WHERE id = ? AND provider = 'groq'", (key_id,))
    conn.commit()
