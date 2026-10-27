# HANDOVER — Vizara Support Agent (Chatbot-as-a-Service)

Everything needed to take over, operate, and extend this project.

---

## 1. What this is

A multi-tenant RAG (retrieval-augmented generation) support chatbot sold as a service:

- Each customer ("client") gets an isolated knowledge base (Chroma collection), its own persona, and its own allowed website origin.
- Customers embed one `<script>` tag on their site; the widget talks to a shared FastAPI backend.
- Answers are grounded **only** in that client's ingested markdown. If nothing relevant is retrieved, the bot escalates to a human instead of guessing.
- Escalations are emailed to the operator via Formspree.
- First customer is **Vizara Studio**; `test` and `acme` exist as demo/test clients.

Live:

| Piece | URL |
|---|---|
| Backend API | https://vizara-support-agent.onrender.com |
| Backend health | https://vizara-support-agent.onrender.com/health |
| Frontend site | https://vizarastudioo.netlify.app |
| Backend repo | https://github.com/Wsif123/vizara-support-agent (`master`) |
| Frontend repo | https://github.com/Wsif123/vizarastudio (`main`) |

Hosting: **Render** (backend, Docker) and **Netlify** (frontend, static). Both auto-deploy on push to the branch listed above.

### Status at handover (Oct 2026)

This is a working prototype with one real customer (Vizara Studio). It is not a finished product — treat it as a codebase to take over, not a running service you must keep alive.

- Backend deploys cleanly on Render; `/health` supports GET + HEAD; widget is served at `/widget/chat-widget.js`.
- Retrieval works and is isolated per client.
- The LLM layer was on dead model IDs during handover. Defaults were updated to Groq `openai/gpt-oss-20b` and Cerebras `gpt-oss-120b` and are env-overridable; an isolated Groq call returned a correct, grounded pricing answer. See §9.2 — **if the deployed service still escalates every message, check the models/keys first.**
- The provided Cerebras key is out of free quota (HTTP 402), so the Cerebras fallback only works once billing is enabled.
- Escalation email requires `NOTIFY_FORMSPREE_URL` to be set; the previous hardcoded form ID was removed before handover.
- Nothing in this repo requires you to keep the existing Groq/Cerebras accounts. You can swap in your own keys and models.

---

## 2. Architecture

```
Visitor's browser
   │  loads customer site (Netlify)
   │  <script src="{backend}/widget/chat-widget.js"
   │          data-api-url="{backend}/chat"
   │          data-client-id="vizara">
   ▼
POST /chat  {session_id, query, client_id}   (+ browser Origin header)
   ▼
FastAPI  src/server.py
   1. client_id in VALID_CLIENT_IDS ?            -> 403 if not
   2. Origin matches CLIENT_DOMAINS[client_id] ?  -> 403 if mismatch
      (requests with no Origin are allowed — curl / dev)
   3. rate limit: 10 req / 60 s per client_id     -> 429 if exceeded
   ▼
src/agent.py
   4. load config/client_{id}.yaml
   5. retrieve top-k chunks from Chroma collection "client_{id}"
      - no chunk >= score_threshold -> escalate "no_relevant_context"
   6. build prompt: persona + retrieved context + last 10 messages
   7. LLM: Groq (key pool, round-robin), model GROQ_MODEL (default openai/gpt-oss-20b)
           -> all keys fail -> Cerebras CEREBRAS_MODEL (default gpt-oss-120b) fallback
           -> both fail -> canned NEEDS_HUMAN escalation (see §9)
   8. model classifies answer as
        ANSWERABLE   -> reply
        OFF_TOPIC    -> polite decline / redirect
        NEEDS_HUMAN  -> ask for email, send escalation via Formspree (if configured)
   9. log exchange to logs/conversations.db (SQLite)
   ▼
JSON {answer, escalated, escalation_reason}
```

Key properties:

- **Single uvicorn worker by design.** Session history, rate-limit counters, and the Chroma collection cache are in-process memory. Do **not** scale to multiple workers/instances without moving those to Redis/DB first.
- **Sessions are not durable.** Chat history lives in RAM and is lost on restart/redeploy (capped at 10 messages per session).
- **Conversation logs** go to `logs/conversations.db` (SQLite). On Render this filesystem is ephemeral — logs disappear on redeploy unless a persistent disk is attached.

---

## 3. Directory layout (backend repo)

```
config/
  client_vizara.yaml      # per-client config (persona, source files, retrieval settings)
  client_test.yaml        # demo client, no content
  client_acme.yaml        # demo client (fake widget company)
data/
  <name>/raw/*.md         # source content (name comes from config "name")
  <name>/processed/       # generated chunk dump (gitignored)
chroma_db/                # Chroma persistence (gitignored; rebuilt in Docker image)
logs/conversations.db     # SQLite conversation log (gitignored)
src/
  server.py               # FastAPI app: allowlist, origin lockdown, rate limit, /chat, /health, /widget static
  agent.py                # RAG orchestration + classification handling + escalation
  retriever.py            # per-client Chroma query (graceful if collection missing)
  ingest.py               # markdown chunking + embedding into client_{id}
  llm_client.py           # Groq key pool + Cerebras fallback
  guardrails.py           # system prompt builder (uses per-client persona) + JSON parsing
  memory.py               # in-memory per-session history + captured email
  logger.py               # SQLite conversation logging
  notifier.py             # Formspree escalation email
  config.py               # YAML config loader (client_{id}.yaml)
eval/
  eval_set.yaml           # regression questions: expected keywords + must_escalate flags
scripts/
  run_eval.py             # runs eval_set.yaml through src.agent.answer and scores it
  debug_retrieval.py      # prints retrieved chunks/scores for a query
  test_queries.py         # ad-hoc retrieval probes
  test_samples.py         # ad-hoc sample/URL probes
  test_three.py           # ad-hoc probes
  test_two.py             # ad-hoc probes
widget/
  chat-widget.js          # canonical widget served at /widget/chat-widget.js
  test.html               # local widget harness
onboard_client.py         # CLI to onboard a new client end-to-end
Dockerfile                # Python 3.11, build-time ingestion, patchelf workaround
requirements.txt
.env.example
```

The **frontend repo** (`vizara-studio`) is a static React-via-Babel single page. It contains its own copy of `chat-widget.js` at the site root and loads it near the end of `index.html`:

```html
<script src="/chat-widget.js"
        data-api-url="https://vizara-support-agent.onrender.com/chat"></script>
```

The widget defaults `data-client-id` to `"vizara"` when the attribute is absent.

---

## 4. Multi-tenancy model

A client is defined by **four** things:

| Thing | Where | Example |
|---|---|---|
| Allowlist + allowed domain | `src/server.py` (`VALID_CLIENT_IDS`, `CLIENT_DOMAINS`) | `"acme": "acme.com"` |
| Config | `config/client_{id}.yaml` | persona, source files, retrieval |
| Content | `data/{config.name}/raw/*.md` | `data/acme/raw/pricing.md` |
| Vector collection | Chroma `client_{id}` | `client_acme` |

Clients are fully isolated: separate collections, separate personas, separate prompts, separate rate limits. An adversarial cross-client query was tested — no leakage.

> Note: `client_vizara.yaml` has `name: vizara-studio`, so its content lives in `data/vizara-studio/`. This mismatch is intentional (legacy folder name). New clients created by the onboarding script use `name: <client_id>` and `data/<client_id>/`.

### 4.1 Onboard a new client

```powershell
# content folder must contain .md / .txt files
python onboard_client.py client_id=acme domain=acme.com content=test_content\acme name="Acme Corp" persona="You are a witty support bot for Acme Corp."
```

The script will:

1. Insert the client into `VALID_CLIENT_IDS` and `CLIENT_DOMAINS` in `src/server.py`
2. Create `config/client_{id}.yaml` (defaults persona from `name` if not supplied)
3. Copy content into `data/{id}/raw/`
4. Ingest into the Chroma collection `client_{id}`
5. Print the widget `<script>` snippet

**Then restart the server (or redeploy).** The allowlist is read at import time — this is the one known onboarding limitation.

### 4.2 Per-client config reference

```yaml
client:
  name: acme                 # data folder under data/
  display_name: Acme Corp
  source_files:              # files in data/<name>/raw/
    - products.md
    - pricing.md
  persona: >                 # injected verbatim at the top of the system prompt
    You are a friendly support agent for Acme Corp...
  escalation:
    email: support@acme.com  # informational only (see note)
    message: ...
  retrieval:
    top_k: 6                 # honored
    score_threshold: 0.5     # honored (similarity floor; below this = escalate)
```

> The `escalation.email` field is currently **not used** by `notifier.py`. Escalation mail goes to the `NOTIFY_EMAIL_TO` env var through the Formspree endpoint configured in `NOTIFY_FORMSPREE_URL`.

---

## 5. Environment variables

Set these in Render (Settings → Environment) and in `.env` locally. `.env` is gitignored — never commit it.

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEYS` | yes* | Comma-separated Groq keys. Rotated round-robin; 429/401 retries next key |
| `SUPPORT_AGENT_GROQ_API_KEY` | fallback* | Legacy single-key variable; used if `GROQ_API_KEYS` is unset |
| `CEREBRAS_API_KEY` | recommended | Fallback provider when every Groq key fails |
| `GROQ_MODEL` | optional | Groq model ID. Default `openai/gpt-oss-20b` |
| `CEREBRAS_MODEL` | optional | Cerebras model ID. Default `gpt-oss-120b` |
| `NOTIFY_FORMSPREE_URL` | optional | Formspree endpoint, e.g. `https://formspree.io/f/<id>`. Empty = no escalation email |
| `NOTIFY_EMAIL_TO` | yes | Address included in escalation emails |
| `PORT` | set by Render | Server port (defaults 8000) |
| `NOTIFY_SMTP_USER` | legacy | **Unused** — the notifier sends via Formspree, not SMTP |
| `NOTIFY_SMTP_APP_PASSWORD` | legacy | **Unused** |

\* One of the two Groq variables must exist or the LLM layer raises at import time.

Models (Oct 2026): Groq `openai/gpt-oss-20b`, Cerebras `gpt-oss-120b` fallback. Both are overridable by env without a code change — important, because providers retire model IDs regularly (the previous defaults, Groq `llama-3.1-8b-instant` and Cerebras `gemma-4-31b`, were both retired and caused every request to fall through to the canned escalation).

---

## 6. Local development

**Python 3.11 is required.** (3.12 has no prebuilt `chroma-hnswlib` wheel; the Docker image is pinned to `python:3.11-slim` for this reason.)

```powershell
cd "D:\wasif\vizara support bot"
py -3.11 -m pip install -r requirements.txt
copy .env.example .env      # then fill in real keys

# build / rebuild the vector index for a client
py -3.11 -c "import sys; sys.path.insert(0,'.'); from src.ingest import ingest; ingest('vizara')"

# run the API
py -3.11 src/server.py      # http://localhost:8000
```

Smoke tests:

```powershell
Invoke-WebRequest http://localhost:8000/health                      # 200
Invoke-WebRequest http://localhost:8000/widget/chat-widget.js       # 200, JS
# chat
Invoke-WebRequest http://localhost:8000/chat -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"s1","query":"what are your prices?","client_id":"vizara"}'
```

Widget harness: open `widget/test.html` (point its script tag at `http://localhost:8000`).

Frontend local dev: the `<script>` tag in `vizara-studio/index.html` points at the **production** backend, so a local page will hit Render. Change `data-api-url` to `http://localhost:8000/chat` while developing.

### Regression eval

`eval/eval_set.yaml` holds question / expected-keyword / must-escalate cases. Run them through the real agent (needs keys + a built index):

```powershell
py -3.11 scripts/run_eval.py
```

`scripts/debug_retrieval.py` prints the chunks and scores behind a query, which is the fastest way to tell whether a bad answer is a retrieval or an LLM problem. The other `scripts/test_*.py` files are ad-hoc probes, not a test suite.

---

## 7. Deployment

### Backend — Render (Docker)

- Auto-deploys `master`.
- `Dockerfile`: Python 3.11-slim, installs `gcc/g++/patchelf`, `pip install`, clears the executable-stack flag on native libs (`patchelf --clear-execstack` — required because `onnxruntime==1.16.3` is the last version that runs without AVX-512, and Render refuses exec-stack binaries), then **runs ingestion at build time** (`ingest('vizara')`) so the image ships with `chroma_db/`.
- Only `vizara` is ingested in the image. Other allowlisted clients (`test`, `acme`) have no collection in production; the retriever now handles that gracefully (returns nothing → escalation) instead of a 500.
- Health check: `/health` supports **GET and HEAD** (Render uses HEAD).
- Image rebuild = fresh `chroma_db`. New/changed client content requires a redeploy.

### Frontend — Netlify

- Auto-deploys `main`, publish directory is the repo root (plain static files).
- `chat-widget.js` is served by Netlify for the Vizara site; the canonical copy for new customers is served by the backend at `/widget/chat-widget.js`.

---

## 8. Security posture (what was audited)

- `.env` is gitignored and never committed; git history was searched for leaked keys — clean.
- No secrets hardcoded in source.
- `client_id` is validated against a strict allowlist before any filesystem/DB use — path traversal, slashes, and SQLi-style values were tested and rejected with 403.
- Origin lockdown per client (browser-embedded widgets always send `Origin`). Requests with **no** Origin are allowed to keep curl/local dev usable — tighten this if you want strict production behavior.
- Per-client rate limit: 10 requests / 60 s (HTTP 429).
- Errors do not leak stack traces or file paths. The origin error echoes only the caller-supplied origin and client_id.
- Prompt-injection probes ("reveal your system prompt", cross-client questions) escalated without leaking prompts or other clients' data.
- `client_secret_*.apps.googleusercontent.com.json` is **not** part of this project and is gitignored. Keep it that way.

**On handover, rotate:** Groq keys, Cerebras key, the Formspree endpoint (or its form), Render account access, Netlify account access, and GitHub repo ownership. Transfer `.env` values out-of-band (password manager), never through git.

---

## 9. Known issues / limitations

1. **New clients require a restart/redeploy** — the allowlist is a module-level constant.
2. **LLM availability is the main failure mode.** Model IDs get retired by providers without notice. When every Groq key and the Cerebras fallback fail, `generate()` returns an error string, `parse_classification_response()` falls back to `NEEDS_HUMAN`, and the customer sees the generic "share your email" message. This is exactly what happened during handover: Groq had retired `llama-3.1-8b-instant` (404) and Cerebras had archived `gemma-4-31b` (404). The defaults are now `openai/gpt-oss-20b` / `gpt-oss-120b`, verified working in an isolated call (correct grounded pricing answer). **The currently supplied Cerebras key is out of free quota (HTTP 402), so Cerebras is not a usable fallback until billing is enabled.** Check `https://api.groq.com/openai/v1/models` and `https://api.cerebras.ai/v1/models` if answers start escalating, then set `GROQ_MODEL` / `CEREBRAS_MODEL` in Render — no code change needed.
3. **Ephemeral storage on Render** — `logs/conversations.db` and `chroma_db/` are lost on redeploy (Chroma is rebuilt at image build; logs are not).
4. **Single worker only** — see §2.
5. **In-memory sessions** — history is lost on restart; no cross-instance sharing.
6. **No admin dashboard** — onboarding is a CLI script; conversation logs are raw SQLite.
7. **Escalation email needs `NOTIFY_FORMSPREE_URL`.** The form ID is no longer hardcoded in `src/notifier.py`. If the variable is unset (or the Formspree form is deleted), escalation emails silently fail and are only logged.
8. **Origin check allows missing Origin** by design; a non-browser attacker can omit it. Rate limiting and the client_id allowlist are the practical controls.
9. **Rate limit is per process**, so it resets on restart.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `403 Invalid or missing client_id` | Client not in `VALID_CLIENT_IDS` in `src/server.py` → add + restart |
| `403 Origin '...' not allowed` | Request origin ≠ `CLIENT_DOMAINS[client_id]` → fix domain mapping |
| `429` | Per-client 10 req/min limit hit → wait or raise `RATE_LIMIT` |
| `500` on chat | Check Render logs. Missing collection is handled; other exceptions are not |
| "I don't have that information — let me connect you with a human." | No chunk above `score_threshold` → improve content or lower threshold |
| All answers escalate / generic "share your email" reply | LLM keys invalid or model IDs retired — check `[llm]` logs; verify models at the provider `/v1/models` endpoint and override `GROQ_MODEL` / `CEREBRAS_MODEL` |
| `ModuleNotFoundError: chromadb` | Wrong interpreter — use Python 3.11 |
| Build fails on `chroma-hnswlib` | Must build on Python 3.11 (cp311 wheel) |
| Render crashes with exec-stack / SIGILL | Keep `patchelf --clear-execstack` + `onnxruntime==1.16.3` pin |
| Escalation email not received | Check `NOTIFY_FORMSPREE_URL` is set, `NOTIFY_EMAIL_TO`, the Formspree form status, and Render logs |

Useful log lines: `[llm] attempt N: Groq key X`, `[llm] all Groq keys exhausted`, `[mem] RSS=...`, `[notifier] ...`.

---

## 11. Product / pricing context

The frontend markets the chatbot service at **$90/mo + $150 one-time setup** (card in `index.html` → `chatBotPlans`). Cost side is dominated by Render (see dashboard) plus Groq/Cerebras usage. The service was designed to be cheap enough for local businesses (barbershops, studios) while sharing one backend.

---

## 12. Handover checklist

- [ ] Transfer GitHub repos `Wsif123/vizara-support-agent` and `Wsif123/vizarastudio`
- [ ] Transfer / re-link Render service and Netlify site
- [ ] Transfer domain / DNS (if custom domains are added later)
- [ ] Deliver `.env` values out-of-band (password manager) — never through git
- [ ] Rotate all API keys and create your own Formspree form after transfer
- [ ] Set `NOTIFY_FORMSPREE_URL` + `NOTIFY_EMAIL_TO` in Render
- [ ] Confirm `GROQ_MODEL` / `CEREBRAS_MODEL` are live at the provider (see §9.2)
- [ ] Verify `/health` returns 200 (GET + HEAD)
- [ ] Verify a live chat message on https://vizarastudioo.netlify.app returns a grounded answer (not the generic escalation)
- [ ] Verify escalation email arrives
- [ ] Run `python onboard_client.py` once in a scratch branch to confirm onboarding works, then revert
