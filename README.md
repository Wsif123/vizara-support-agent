# Vizara Support Agent

Multi-tenant RAG support chatbot sold as a service. Each client gets an isolated knowledge base, its own persona, and an origin-locked widget embed. Answers are grounded in that client's content; anything not covered escalates to a human by email.

**Live:** https://vizara-support-agent.onrender.com · **Frontend:** https://vizarastudioo.netlify.app

> Full architecture, deployment, onboarding, security notes, and troubleshooting: **[HANDOVER.md](HANDOVER.md)**
> Operator console design and payment workflow: **[DASHBOARD_SPEC.md](DASHBOARD_SPEC.md)** · **[PAYMENTS.md](PAYMENTS.md)**
> Audit findings and residual risks: **[SECURITY_AUDIT.md](SECURITY_AUDIT.md)**

## Stack

- **LLM:** Groq only — `openai/gpt-oss-20b` by default, overridable with `GROQ_MODEL` or the console Settings page. Multiple keys are used in weighted round-robin with automatic cooldowns.
- **Embeddings:** Chroma built-in (ONNX all-MiniLM-L6-v2)
- **Vector store:** Chroma 0.5.3 (persistent, per-client collections)
- **Server:** FastAPI + uvicorn (single worker)
- **Widget:** vanilla JS, served at `/widget/chat-widget.js`
- **Logs:** SQLite locally; optional Neon/Postgres when `DATABASE_URL` is set
- **Operator console:** local-only FastAPI app on `127.0.0.1:8787` (not deployed, not customer-facing)

## Quick start (Python 3.11 required)

```bash
py -3.11 -m pip install -r requirements.txt
cp .env.example .env          # fill in GROQ_API_KEYS, NOTIFY_EMAIL_TO, optional NOTIFY_FORMSPREE_URL
py -3.11 -c "import sys; sys.path.insert(0,'.'); from src.ingest import ingest; ingest('vizara')"
py -3.11 src/server.py        # http://localhost:8000
```

Start the operator console by double-clicking **`start-dashboard.bat`**, or:

```bash
py -3.11 -m uvicorn admin.app:app --host 127.0.0.1 --port 8787
```

The first visit asks you to create the operator password. The two Groq keys already in `.env` are imported once into encrypted local storage (`admin/admin.db` + `admin/.secret.key`, both git-ignored).

Test the widget: open `widget/test.html`.

## API

```http
POST /chat
{ "session_id": "abc", "query": "what are your prices?", "client_id": "vizara" }

200 { "answer": "...", "escalated": false, "escalation_reason": null }
403 invalid/disabled client_id or origin
429 rate limit (10 req / 60 s per visitor, 120 req / 60 s per client)
```

`GET|HEAD /health` → `{"status":"ok"}`

## Onboard a new client

The normal path is the console: **Clients → New client**. It writes the YAML config, saves content, builds the index, creates the billing record, and shows the exact widget snippet — no `server.py` edits.

CLI equivalent:

```bash
python onboard_client.py client_id=acme domain=acme.com content=path/to/content name="Acme Corp" contact_name="Acme Support"
```

The public server reads `config/client_*.yaml` through a small registry (2-second cache). To pause a client, set `enabled: false` (or use the console); requests are rejected immediately. Changed source files are picked up at startup by the stale-index check, or immediately with the console's Reindex button.

## Deployment

- Backend: Render (Docker), auto-deploy on `master`. Build-time ingestion; `onnxruntime==1.16.3` + `patchelf --clear-execstack` are required on Render.
- Frontend: Netlify, auto-deploy on `main` (static site + `chat-widget.js`).
- The operator console is **never** deployed. Its database and encryption key stay on the operator PC.

See [HANDOVER.md](HANDOVER.md) for the full operational runbook.
