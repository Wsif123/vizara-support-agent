# Vizara Support Agent

Multi-tenant RAG support chatbot sold as a service. Each client gets an isolated knowledge base, its own persona, and an origin-locked widget embed. Answers are grounded in that client's content; anything not covered escalates to a human by email.

**Live:** https://vizara-support-agent.onrender.com · **Frontend:** https://vizarastudioo.netlify.app

> Full architecture, deployment, onboarding, security notes, and troubleshooting: **[HANDOVER.md](HANDOVER.md)**

## Stack

- **LLM:** Groq `llama-3.1-8b-instant` (key pool, round-robin) with Cerebras `gemma-4-31b` fallback
- **Embeddings:** Chroma built-in (ONNX all-MiniLM-L6-v2)
- **Vector store:** Chroma 0.5.3 (persistent, per-client collections)
- **Server:** FastAPI + uvicorn (single worker)
- **Widget:** vanilla JS, served at `/widget/chat-widget.js`
- **Logs:** SQLite (`logs/conversations.db`)

## Quick start (Python 3.11 required)

```bash
py -3.11 -m pip install -r requirements.txt
cp .env.example .env          # fill in GROQ_API_KEYS / CEREBRAS_API_KEY / NOTIFY_EMAIL_TO
py -3.11 -c "import sys; sys.path.insert(0,'.'); from src.ingest import ingest; ingest('vizara')"
py -3.11 src/server.py        # http://localhost:8000
```

Test the widget: open `widget/test.html`.

## API

```http
POST /chat
{ "session_id": "abc", "query": "what are your prices?", "client_id": "vizara" }

200 { "answer": "...", "escalated": false, "escalation_reason": null }
403 invalid client_id / origin
429 rate limit (10 req / 60 s per client)
```

`GET|HEAD /health` → `{"status":"ok"}`

## Onboard a new client

```bash
python onboard_client.py client_id=acme domain=acme.com content=path/to/content name="Acme Corp"
```

Adds the client to the allowlist and domain map, creates `config/client_acme.yaml`, copies content into `data/acme/raw/`, ingests the `client_acme` Chroma collection, and prints the embed snippet. **Restart the server afterwards.**

## Deployment

- Backend: Render (Docker), auto-deploy on `master`. Build-time ingestion; `onnxruntime==1.16.3` + `patchelf --clear-execstack` are required on Render.
- Frontend: Netlify, auto-deploy on `main` (static site + `chat-widget.js`).

See [HANDOVER.md](HANDOVER.md) for the full operational runbook.
