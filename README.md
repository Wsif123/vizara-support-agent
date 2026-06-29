# Vizara Support Agent

A lightweight RAG chat agent for Vizara Studio — answers customer questions about video ads and product photography, grounded in the actual Fiverr gig content.

## Stack

- **LLM:** Groq API (llama-3.1-8b-instant)
- **Embeddings:** sentence-transformers (all-MiniLM-L6-v2)
- **Vector store:** Chroma (persistent, file-based)
- **Server:** FastAPI
- **Widget:** Vanilla JS (no framework)

## Required env vars

Set these in your deployment environment (Render dashboard, .env for local):

| Variable | Description |
|---|---|
| `SUPPORT_AGENT_GROQ_API_KEY` | Groq API key for the LLM |
| `NOTIFY_SMTP_USER` | Gmail address for sending escalation emails |
| `NOTIFY_SMTP_APP_PASSWORD` | Gmail app password (not regular password) |
| `NOTIFY_EMAIL_TO` | Where escalation emails get delivered |

`NOTIFY_*` vars are optional — without them, escalations are logged but no email is sent.

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in your Groq key
python src/ingest.py    # build the vector store
uvicorn src.server:app --host 0.0.0.0 --port 8000
```

Open `widget/test.html` in a browser to test the chat widget.

## Deployment (Render)

1. Create a new Web Service on Render, pointing at this repo
2. **Start command:** `python src/server.py` (reads `$PORT` env var automatically)
3. Set the 4 env vars above in Render's dashboard
4. Deploy — the server auto-runs ingestion on first boot if `chroma_db/` is empty

No build step needed. Render auto-detects Python and runs `pip install -r requirements.txt`.
