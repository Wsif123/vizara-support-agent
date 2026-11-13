"""Host-header validation, security headers, and coarse request throttling."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_READ_LIMIT = 600
_WRITE_LIMIT = 60
_WINDOW = 60.0

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _host_only(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value.split("]", 1)[0].lstrip("[")
    return value.split(":", 1)[0]


def _bucket(request: Request) -> str:
    token = request.cookies.get("vizara_admin_session", "")
    if token:
        return "s:" + token[:24]
    return "ip:" + (request.client.host if request.client else "unknown")


def _under_limit(key: str, limit: int) -> bool:
    now = time.time()
    with _lock:
        window = _hits[key]
        while window and window[0] < now - _WINDOW:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        if len(_hits) > 5000:
            for k in list(_hits):
                if not _hits[k]:
                    del _hits[k]
        return True


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _host_only(request.headers.get("host", "")) not in ALLOWED_HOSTS:
            return JSONResponse(
                {"detail": "Invalid Host header. This console only answers on localhost."},
                status_code=400,
            )

        limit = _WRITE_LIMIT if request.method not in {"GET", "HEAD", "OPTIONS"} else _READ_LIMIT
        if request.url.path.startswith("/static/"):
            limit = 1000
        if not _under_limit(_bucket(request) + ":" + request.method, limit):
            return JSONResponse(
                {"detail": "Too many requests. Slow down and try again shortly."},
                status_code=429,
            )

        response: Response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
