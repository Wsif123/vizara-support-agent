import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

# Ensure the repo root is on sys.path when running python src/server.py
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import psutil

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.agent import answer

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

# Hardcoded client ID allowlist for multi-tenancy step 1
VALID_CLIENT_IDS = {"vizara", "test", "acme"}

# Client ID -> allowed domain mapping for Origin validation
CLIENT_DOMAINS = {
    "vizara": "vizarastudioo.netlify.app",
    "test": None,
    "acme": "acme.com",
}

# Per-client rate limiting: 10 requests per 60-second sliding window
RATE_LIMIT = 10
RATE_WINDOW = 60
_client_requests: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(client_id: str):
    now = time.time()
    window = _client_requests[client_id]
    while window and window[0] < now - RATE_WINDOW:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="You're sending messages too fast. Please wait a moment and try again.",
        )
    window.append(now)


app = FastAPI(title="Vizara Support Agent")

# CORS kept wide â€” per-client origin validation happens at the endpoint level
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _log_memory(label: str):
    process = psutil.Process()
    rss_mb = process.memory_info().rss / 1024 / 1024
    print(f"[mem] {label}: RSS={rss_mb:.1f} MB", flush=True)


@app.on_event("startup")
def bootstrap():
    _log_memory("startup-before")
    if not CHROMA_DIR.exists() or not any(CHROMA_DIR.iterdir()):
        print("[boot] chroma_db missing or empty â€” running ingestion...")
        from src.ingest import ingest
        ingest("vizara")
        print("[boot] ingestion complete")
    else:
        print("[boot] chroma_db found â€” skipping ingestion")
    _log_memory("startup-after")


class ChatRequest(BaseModel):
    session_id: str
    query: str
    client_id: str


class ChatResponse(BaseModel):
    answer: str
    escalated: bool = False
    escalation_reason: str = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, origin: str = Header(None)):
    if not req.client_id or req.client_id not in VALID_CLIENT_IDS:
        raise HTTPException(
            status_code=403,
            detail=f"Invalid or missing client_id. Valid values: {sorted(VALID_CLIENT_IDS)}",
        )

    # Origin validation: reject if Origin doesn't match the client's registered domain
    # Allow requests with no Origin header (curl, localhost, server-to-server) for dev testing
    allowed_domain = CLIENT_DOMAINS.get(req.client_id)
    if origin is not None and allowed_domain is not None:
        parsed = urlparse(origin)
        request_domain = parsed.hostname
        if request_domain != allowed_domain:
            raise HTTPException(
                status_code=403,
                detail=f"Origin '{origin}' not allowed for client_id '{req.client_id}'",
            )

    # Per-client rate limiting
    _check_rate_limit(req.client_id)

    result = answer(req.session_id, req.query, req.client_id)
    _log_memory("after-request")
    return ChatResponse(**result)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.server:app", host="0.0.0.0", port=port)
