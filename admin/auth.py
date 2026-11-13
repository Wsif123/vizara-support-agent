"""Password hashing, server-side sessions, CSRF tokens, login throttling."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

COOKIE_NAME = "vizara_admin_session"
PREAUTH_COOKIE = "vizara_csrf"
SESSION_IDLE = timedelta(hours=12)
SESSION_ABSOLUTE = timedelta(days=7)
COOKIE_SECURE = os.getenv("VIZARA_COOKIE_SECURE", "0") == "1"

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_LEN = 32

_login_attempts: dict[str, deque] = defaultdict(deque)
_login_lock = threading.Lock()
_LOGIN_LIMIT = 5
_LOGIN_WINDOW = 60.0


class AuthRequired(Exception):
    """Raised when a page needs a signed-in operator."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_LEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 12:
        return "Use at least 12 characters."
    if len(password) > 200:
        return "That password is too long (200 characters max)."
    if password.lower() in {"password1234", "administrator", "letmein12345"}:
        return "Choose a less predictable password."
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    conn: sqlite3.Connection,
    request: Request,
    ttl: timedelta = SESSION_ABSOLUTE,
) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = utcnow()
    conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (_iso(now),))
    conn.execute(
        """
        INSERT INTO admin_sessions
            (token_hash, csrf_token, created_at, last_seen_at, expires_at, ip, user_agent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _token_hash(token),
            csrf,
            _iso(now),
            _iso(now),
            _iso(now + min(ttl, SESSION_ABSOLUTE)),
            _client_ip(request),
            (request.headers.get("user-agent") or "")[:250],
        ),
    )
    conn.commit()
    return token, {"csrf_token": csrf, "token_hash": _token_hash(token)}


def get_session(request: Request, conn: sqlite3.Connection) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) < 30:
        return None
    digest = _token_hash(token)
    row = conn.execute(
        "SELECT * FROM admin_sessions WHERE token_hash = ?", (digest,)
    ).fetchone()
    if not row:
        return None

    now = utcnow()
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        last_seen = datetime.fromisoformat(row["last_seen_at"])
    except ValueError:
        conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (digest,))
        conn.commit()
        return None

    if now >= expires or now - last_seen > SESSION_IDLE:
        conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (digest,))
        conn.commit()
        return None

    # Touch at most once a minute to keep SQLite writes low.
    if now - last_seen > timedelta(seconds=60):
        conn.execute(
            "UPDATE admin_sessions SET last_seen_at = ? WHERE token_hash = ?",
            (_iso(now), digest),
        )
        conn.commit()

    session = dict(row)
    session["token_hash"] = digest
    return session


def delete_session(conn: sqlite3.Connection, token_hash: str) -> None:
    conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()


def delete_other_sessions(conn: sqlite3.Connection, keep_hash: str) -> int:
    cur = conn.execute(
        "DELETE FROM admin_sessions WHERE token_hash != ?", (keep_hash,)
    )
    conn.commit()
    return cur.rowcount


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_ABSOLUTE.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")


def preauth_token(request: Request) -> str:
    token = request.cookies.get(PREAUTH_COOKIE)
    if not token or len(token) < 30:
        token = secrets.token_urlsafe(32)
    return token


def set_preauth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        PREAUTH_COOKIE,
        token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=COOKIE_SECURE,
        path="/",
    )


def new_preauth_token(response: Response, request: Request) -> str:
    token = preauth_token(request)
    set_preauth_cookie(response, token)
    return token


def preauth_csrf_ok(request: Request, submitted: str | None) -> bool:
    cookie = request.cookies.get(PREAUTH_COOKIE)
    return bool(cookie and submitted and hmac.compare_digest(cookie, submitted))


def csrf_ok(session: dict | None, submitted: str | None) -> bool:
    if not session or not submitted:
        return False
    return hmac.compare_digest(session.get("csrf_token", ""), submitted)


def _client_ip(request: Request) -> str:
    # Local-only app: never trust proxy headers. A spoofed X-Forwarded-For
    # would otherwise let someone bypass the login throttle.
    return request.client.host if request.client else "unknown"


def login_allowed(request: Request) -> bool:
    ip = _client_ip(request)
    now = time.time()
    with _login_lock:
        window = _login_attempts[ip]
        while window and window[0] < now - _LOGIN_WINDOW:
            window.popleft()
        return len(window) < _LOGIN_LIMIT


def login_failed(request: Request) -> None:
    with _login_lock:
        _login_attempts[_client_ip(request)].append(time.time())


def login_succeeded(request: Request) -> None:
    with _login_lock:
        _login_attempts.pop(_client_ip(request), None)


def set_flash(
    conn: sqlite3.Connection, token_hash: str, message: str, kind: str = "ok"
) -> None:
    conn.execute(
        "UPDATE admin_sessions SET flash_message = ?, flash_kind = ? WHERE token_hash = ?",
        (message[:500], kind, token_hash),
    )
    conn.commit()


def take_flash(conn: sqlite3.Connection, token_hash: str) -> tuple[str | None, str]:
    row = conn.execute(
        "SELECT flash_message, flash_kind FROM admin_sessions WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    if not row or not row["flash_message"]:
        return None, "ok"
    conn.execute(
        "UPDATE admin_sessions SET flash_message = NULL, flash_kind = NULL WHERE token_hash = ?",
        (token_hash,),
    )
    conn.commit()
    return row["flash_message"], row["flash_kind"] or "ok"
