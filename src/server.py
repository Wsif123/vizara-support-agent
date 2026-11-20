import ipaddress
import os
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

# Ensure the repo root is on sys.path when running python src/server.py
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import psutil

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import logger
from src.agent import answer
from src.ingest import ingest, index_is_stale
from src.registry import all_clients, client_domains, get_client, valid_client_ids


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Per-client/per-visitor rate limiting: configurable, default 30 requests per
# 60-second sliding window. GLOBAL_RATE_LIMIT is the client-wide safety net
# against a caller rotating IPs.
RATE_LIMIT = _positive_env_int("RATE_LIMIT", 30)
GLOBAL_RATE_LIMIT = _positive_env_int("GLOBAL_RATE_LIMIT", 300)
RATE_WINDOW = 60

_client_requests: dict[str, deque] = defaultdict(deque)
_client_global: dict[str, deque] = defaultdict(deque)


def _prune(window: deque, now: float):
    while window and window[0] < now - RATE_WINDOW:
        window.popleft()


def _check_window(key: str, limit: int, store: dict[str, deque], now: float):
    window = store[key]
    _prune(window, now)
    if len(window) >= limit:
        raise HTTPException(
            status_code=429,
            detail="You're sending messages too fast. Please wait a moment and try again.",
        )
    window.append(now)


def _trust_proxy_headers() -> bool:
    """Only trust forwarding headers when we know a proxy sets them.

    On Render the platform edge appends the real client address. Locally there
    is no proxy, so a caller-supplied X-Forwarded-For must be ignored or the
    per-IP rate limit can be bypassed by rotating a fake header.
    """
    explicit = os.getenv("TRUST_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
    return explicit or bool(os.getenv("RENDER"))


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    # X-Forwarded-For is "client, proxy1, proxy2". The right-most entry is the
    # one added by our trusted proxy; anything before it can be spoofed.
    if _trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            candidate = _valid_ip(forwarded.rsplit(",", 1)[-1])
            if candidate:
                return candidate
        real_ip = _valid_ip(request.headers.get("x-real-ip", ""))
        if real_ip:
            return real_ip
    return request.client.host if request.client else "unknown"


def _check_rate_limit(client_id: str, ip: str):
    now = time.time()
    _check_window(f"{client_id}:{ip}", RATE_LIMIT, _client_requests, now)
    _check_window(f"global:{client_id}", GLOBAL_RATE_LIMIT, _client_global, now)

    # Keep the per-IP map from growing forever.
    if len(_client_requests) > 10000:
        for key in list(_client_requests):
            _prune(_client_requests[key], now)
            if not _client_requests[key]:
                del _client_requests[key]


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    bootstrap()
    yield


app = FastAPI(title="Vizara Support Agent", lifespan=_lifespan)

# Serve the embeddable widget from the backend so onboarding a client only
# needs one URL: {backend}/widget/chat-widget.js
WIDGET_DIR = Path(__file__).parent.parent / "widget"
if WIDGET_DIR.exists():
    app.mount("/widget", StaticFiles(directory=str(WIDGET_DIR)), name="widget")

# CORS stays wide because the widget is embedded on customer domains; the real
# per-client origin check happens in the /chat handler. Credentials are off:
# this API has no cookies or auth, and "*" + credentials is an unsafe pairing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _log_memory(label: str):
    process = psutil.Process()
    rss_mb = process.memory_info().rss / 1024 / 1024
    print(f"[mem] {label}: RSS={rss_mb:.1f} MB", flush=True)


def bootstrap():
    logger.init_db()
    _log_memory("startup-before")
    for client_id, item in sorted(all_clients().items()):
        if not item["enabled"] or not item["config"].get("source_files"):
            continue
        try:
            if index_is_stale(client_id):
                print(f"[boot] index for '{client_id}' missing/stale — running ingestion...")
                ingest(client_id)
                print(f"[boot] ingestion complete for '{client_id}'")
        except Exception as exc:
            # Never take the whole API down because one tenant's content is bad.
            print(f"[boot] index check failed for '{client_id}': {exc}")
    _log_memory("startup-after")


class ChatRequest(BaseModel):
    # Bound the inputs: an unbounded query is an easy way to burn LLM quota.
    session_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=2000)
    client_id: str = Field(min_length=1, max_length=64)


class ChatResponse(BaseModel):
    answer: str
    escalated: bool = False
    escalation_reason: str | None = None


@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request, origin: str = Header(None)):
    if not req.client_id or req.client_id not in valid_client_ids():
        raise HTTPException(status_code=403, detail="Unknown or disabled client_id.")

    # Origin validation: reject if Origin doesn't match the client's registered domain
    # Allow requests with no Origin header (curl, localhost, server-to-server) for dev testing
    allowed_domain = client_domains().get(req.client_id)
    if origin is not None and allowed_domain is not None:
        parsed = urlparse(origin)
        request_domain = parsed.hostname
        if request_domain != allowed_domain:
            raise HTTPException(
                status_code=403,
                detail=f"Origin '{origin}' not allowed for client_id '{req.client_id}'",
            )

    # Per-client/per-IP rate limiting
    _check_rate_limit(req.client_id, _client_ip(request))

    client = get_client(req.client_id)
    billing = (client or {}).get("config", {}).get("billing") or {}
    included = billing.get("conversations_included")
    if isinstance(included, int) and not isinstance(included, bool) and included > 0:
        if logger.usage_count(req.client_id) >= included:
            raise HTTPException(
                status_code=429,
                detail=(
                    "This assistant has reached its monthly conversation limit. "
                    "Please contact us to raise it."
                ),
            )
    logger.bump_usage(req.client_id)

    result = answer(req.session_id, req.query, req.client_id)
    _log_memory("after-request")
    return ChatResponse(**result)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.server:app", host="0.0.0.0", port=port)
