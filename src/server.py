import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path when running python src/server.py
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.agent import answer

CHROMA_DIR = Path(__file__).parent.parent / "chroma_db"

app = FastAPI(title="Vizara Support Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://vizara-studio.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def bootstrap():
    if not CHROMA_DIR.exists() or not any(CHROMA_DIR.iterdir()):
        print("[boot] chroma_db missing or empty — running ingestion...")
        from src.ingest import ingest
        ingest("vizara-studio")
        print("[boot] ingestion complete")
    else:
        print("[boot] chroma_db found — skipping ingestion")


class ChatRequest(BaseModel):
    session_id: str
    query: str


class ChatResponse(BaseModel):
    answer: str
    escalated: bool = False
    escalation_reason: str = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    result = answer(req.session_id, req.query)
    return ChatResponse(**result)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("src.server:app", host="0.0.0.0", port=port)
