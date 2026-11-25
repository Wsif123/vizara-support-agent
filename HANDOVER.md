# HANDOVER — Vizara Support Agent (Chatbot-as-a-Service)

Everything needed to take over, operate, and extend this project.

**Last updated:** 13 November 2026

---

## 1. What this is

A multi-tenant RAG (retrieval-augmented generation) support chatbot sold as a service:

- Each customer ("client") gets an isolated knowledge base (Chroma collection), its own persona, and its own allowed website origin.
- Customers embed one `<script>` tag on their site; the widget talks to a shared FastAPI backend.
- Answers are grounded **only** in that client's ingested markdown. If nothing relevant is retrieved, the bot escalates to a human instead of guessing.
- Escalations can be emailed to the operator through Formspree (optional; events are always logged).
- First customer is **Vizara Studio**; `test` and `acme` exist as demo/test clients.
- The **operator console** is a separate, local-only back office for clients, billing, keys, and transcripts. It is never deployed and never customer-facing.

Live:

| Piece | URL |
|---|---|
| Backend API | https://vizara-support-agent.onrender.com |
| Backend health | https://vizara-support-agent.onrender.com/health |
| Frontend site | https://vizarastudioo.netlify.app |
| Backend repo | https://github.com/Wsif123/vizara-support-agent (`master`) |
| Frontend repo | https://github.com/Wsif123/vizarastudio (`main`) |

Hosting: **Render** (backend, Docker) and **Netlify** (frontend, static). Both auto-deploy on push to the branch listed above.

### Status at handover (November 2026)

This is a working prototype with one real customer (Vizara Studio). It is a codebase to take over, not a finished product.

- Backend deploys cleanly on Render; `/health` supports GET + HEAD; the widget is served at `/widget/chat-widget.js`.
- Retrieval works and is isolated per client. Local indexes: vizara 35 chunks, acme 5 chunks.
- **Groq only.** Cerebras has been removed from code and configuration. The default model is `openai/gpt-oss-20b`; two Groq keys exist in the local encrypted pool.
- A live Groq call through the encrypted pool was verified (`Hello`, 170 tokens, ~1.4 s) and usage was recorded.
- The operator console is built and passes a 43-check end-to-end smoke test. It runs only on `127.0.0.1:8787`.
- **The Render deployment still runs older, uncommitted code.** Push and redeploy to ship the current registry/key-pool/widget changes. Until then, the live site keeps the previous behavior, including the stale Vizara index that predates `chatbot-service.md`.
- Escalation email requires `NOTIFY_FORMSPREE_URL`; it is not set in the local `.env` at handover. Escalations are still logged.
- Payments use hosted payment links and manual reconciliation in the console. There are no automated checkout webhooks yet. See `PAYMENTS.md`.
- Nothing in this repo requires the existing Groq account. Swap in your own keys and model IDs at any time.

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
   1. client_id in registry.valid_client_ids() ?  -> 403 if not/disabled
   2. Origin matches registry.client_domains()[client_id] ? -> 403 if mismatch
      (requests with no Origin are allowed — curl / local dev)
   3. rate limit: 10 req / 60 s per client_id+IP (120 req / 60 s per client) -> 429
   ▼
src/agent.py
   4. load client config through src/registry.py (2-second cache)
   5. retrieve top-k chunks from Chroma collection "client_{id}"
      - no chunk >= score_threshold -> escalate "no_relevant_context"
   6. build prompt: persona + retrieved context + last 10 messages
   7. LLM: Groq key pool, model GROQ_MODEL (default openai/gpt-oss-20b)
           -> per-key retry, 429 cooldowns, next key on failure
           -> all keys fail -> canned NEEDS_HUMAN escalation (see §11)
   8. model classifies answer as
        ANSWERABLE   -> reply
        OFF_TOPIC    -> polite decline / redirect
        NEEDS_HUMAN  -> ask for email, send escalation via Formspree (if configured)
   9. log exchange to logs/conversations.db (SQLite)
   ▼
JSON {answer, escalated, escalation_reason}
```

Key properties:

- **Clients are config-driven.** `src/registry.py` reads `config/client_*.yaml` with a 2-second cache. A new YAML file with `enabled: true` registers a tenant; no `server.py` edit and no restart are required. This is the main architectural change from the older allowlist-in-code model.
- **Indexes self-heal at startup.** `index_is_stale()` hashes each client's `source_files` against `data/<name>/processed/manifest.json`. If a file changed, was added, or the collection is missing/count-mismatched, the server re-ingests that client before serving traffic.
- **Section headings are embedded with each chunk.** Values stored only in a heading (for example the chatbot's `$90/month`) remain retrievable. `ingest.py` prefixes the heading to the chunk text before embedding.
- **Single uvicorn worker by design.** Session history, rate-limit counters, and the Chroma collection cache are in-process. Do **not** scale to multiple workers/instances without moving those to Redis/DB first.
- **Sessions are not durable.** Chat history lives in RAM and is lost on restart/redeploy (capped at 10 messages per session).
- **Conversation logs** go to `logs/conversations.db` (SQLite). On Render this filesystem is ephemeral — logs disappear on redeploy unless a persistent disk is attached. The console can instead read a Neon/Postgres table when `DATABASE_URL` is set.

---

## 3. Directory layout (backend repo)

```
config/
  client_vizara.yaml      # per-client config (persona, domain, source files, retrieval)
  client_test.yaml        # demo client, no content
  client_acme.yaml        # demo client (fake widget company)
data/
  <name>/raw/*.md         # source content (name comes from config "name")
  <name>/processed/       # generated chunk dump + ingest manifest (gitignored)
chroma_db/                # Chroma persistence (gitignored; rebuilt in Docker image)
logs/conversations.db     # SQLite conversation log (gitignored)
src/
  server.py               # FastAPI app: registry, origin lockdown, rate limit, /chat, /health, /widget
  agent.py                # RAG orchestration + classification handling + escalation
  registry.py             # config-driven client registry (2-second cache)
  retriever.py            # per-client Chroma query (graceful if collection missing)
  vectorstore.py          # shared Chroma PersistentClient
  ingest.py               # markdown chunking + embedding into client_{id}; stale-index check
  llm_client.py           # Groq-only generation, per-key retry
  key_store.py            # encrypted Groq pool shared with the operator console; env fallback
  guardrails.py           # system prompt builder (per-client persona) + JSON parsing
  memory.py               # in-memory per-session history + captured email
  logger.py               # SQLite conversation logging
  notifier.py             # Formspree escalation email
  config.py               # YAML config loader (client_{id}.yaml)
admin/                    # local-only operator console (never deployed)
  app.py                  # FastAPI routes for all pages
  auth.py                 # scrypt password, sessions, CSRF, login throttle
  db.py                   # SQLite helpers + migrations
  keys.py                 # encrypted Groq key management
  billing.py              # plans, subscriptions, invoices, payment links
  metrics.py              # SQLite/Postgres analytics
  onboarding.py           # client creation: YAML + content + ingest + record
  security.py             # Host allowlist, CSP, security headers, throttling
  templates/              # Jinja2 pages
  static/                 # app.css, app.js (no CDN, no Node)
  migrations/             # admin schema
  mockup.html             # approved visual reference (static, not served)
eval/
  eval_set.yaml           # regression questions: expected keywords + must_escalate flags
scripts/
  run_eval.py             # runs eval_set.yaml through src.agent.answer and scores it
  debug_retrieval.py      # prints retrieved chunks/scores for a query
  test_*.py               # ad-hoc probes, not a test suite
widget/
  chat-widget.js          # canonical widget served at /widget/chat-widget.js
  test.html               # local widget harness
onboard_client.py         # CLI onboarding helper (console is the primary flow)
start-dashboard.bat       # one-click local console launcher
Dockerfile                # Python 3.11, build-time ingestion, patchelf workaround
requirements.txt
.env.example
DASHBOARD_SPEC.md         # console design and data model
PAYMENTS.md               # payment-provider runbook
```

The **frontend repo** (`vizara-studio`) is a static React-via-Babel single page. It contains its own copy of `chat-widget.js` at the site root and loads it near the end of `index.html`:

```html
<script src="/chat-widget.js"
        data-api-url="https://vizara-support-agent.onrender.com/chat"></script>
```

The widget defaults `data-client-id` to `"vizara"` when the attribute is absent. Per-client branding uses optional attributes: `data-brand-name` (header/brand copy), `data-greeting`, `data-followup-name` (name shown in the escalation badge), and `data-header-sub`. The console and `onboard_client.py` emit a snippet with the relevant values filled in.

---

## 4. Multi-tenancy model

A client is defined by **four** things:

| Thing | Where | Example |
|---|---|---|
| Registry entry + allowed domain | `config/client_{id}.yaml` (`domain`, `enabled`) | `"acme": "acme.com"` |
| Config | `config/client_{id}.yaml` | persona, source files, retrieval |
| Content | `data/{config.name}/raw/*.md` | `data/acme/raw/pricing.md` |
| Vector collection | Chroma `client_{id}` | `client_acme` |

Clients are fully isolated: separate collections, personas, prompts, and rate limits. An adversarial cross-client query was tested — no leakage.

> Note: `client_vizara.yaml` has `name: vizara-studio`, so its content lives in `data/vizara-studio/`. This mismatch is intentional (legacy folder name). New clients created by the console or CLI use `name: <client_id>` and `data/<client_id>/`.

### 4.1 Onboard a new client (primary: console)

1. Open the console (`start-dashboard.bat`).
2. Go to **Clients → New client**.
3. Fill in client ID (lowercase slug), display name, allowed domain, contact details, optional persona, retrieval settings, and paste markdown knowledge (or point at existing content).
4. Submit. The console writes `config/client_{id}.yaml`, saves `data/{id}/raw/knowledge.md`, runs ingestion into `client_{id}`, and creates the client record/optional subscription.
5. Copy the widget snippet from the client page.

The public server picks up the new YAML within 2 seconds. No restart, no `server.py` edits.

CLI equivalent (still supported):

```powershell
python onboard_client.py client_id=acme domain=acme.com content=test_content\acme name="Acme Corp" contact_name="Acme Support" persona="You are a witty support bot for Acme Corp."
```

The CLI creates the YAML, copies content into `data/{id}/raw/`, runs ingestion, and prints the widget snippet. It refuses to overwrite an existing config.

To pause a client, set `enabled: false` in the YAML or use the console. Requests are rejected on the next registry refresh (≤2 s).

### 4.2 Per-client config reference

```yaml
client:
  name: acme                 # data folder under data/
  display_name: Acme Corp
  domain: acme.com           # origin lock; blank = no origin restriction
  enabled: true              # false = all /chat requests rejected
  source_files:              # files in data/<name>/raw/
    - products.md
    - pricing.md
  persona: >                 # injected verbatim at the top of the system prompt
    You are a friendly support agent for Acme Corp...
  escalation:
    email: support@acme.com  # informational only (see note)
    contact_name: Acme Support  # used in the escalation chat copy
    message: ...
  retrieval:
    top_k: 6                 # honored
    score_threshold: 0.5     # honored (similarity floor; below this = escalate)
```

> `escalation.email` is **not used** by `notifier.py`. Escalation mail goes to the `NOTIFY_EMAIL_TO` env var through the Formspree endpoint configured in `NOTIFY_FORMSPREE_URL`. `escalation.contact_name` (falling back to `display_name`) is used in customer-facing escalation messages.

---

## 5. Operator console (local only)

The console is a FastAPI + Jinja2 + SQLite app served on **`127.0.0.1:8787`**. It is deliberately not reachable from other machines and must never be deployed.

**Start it:** double-click `start-dashboard.bat`, or:

```powershell
py -3.11 -m uvicorn admin.app:app --host 127.0.0.1 --port 8787
```

**First run:** `/setup` asks for the operator password (minimum 12 characters). Passwords are hashed with scrypt (N=16384, r=8, p=1). Sessions are server-side, HttpOnly, SameSite=Strict; idle timeout 12 hours, absolute lifetime 7 days.

### Pages

| Page | What it does |
|---|---|
| Overview | Conversation volume, escalations, active clients, Groq key health, recent activity |
| Clients | List, search, enable/disable, open a client |
| New client | Full onboarding form (config + knowledge + ingest + record) |
| Client detail | Five tabs: Overview, Conversations, Billing, Content, Settings |
| Conversations | Search/filter transcripts, escalation state, CSV export |
| Billing | Plans, subscriptions, invoices, payment links, record payment, exports |
| API keys | Encrypted Groq keys: add/test/toggle/weight/delete, live load and events |
| Settings | Runtime status, model, security info, backups, local files |

### Data and secrets

| Item | Path | Notes |
|---|---|---|
| Console database | `admin/admin.db` | SQLite, WAL mode; git-ignored |
| Encryption key | `admin/.secret.key` | 32 random bytes, mode-restricted; git-ignored |
| Backups | `admin/backups/` | SQLite snapshots; git-ignored |
| Bot transcripts | `logs/conversations.db` | Separate from console data |
| Live transcripts (optional) | `DATABASE_URL` | Neon/Postgres; requires `psycopg` |

Groq keys are encrypted at rest (Fernet) with `admin/.secret.key`, shown only masked (`gsk_…WEoH`), excluded from exports, and never logged. On first run the console imports `GROQ_API_KEYS` / `SUPPORT_AGENT_GROQ_API_KEY` once so the existing keys appear without re-pasting.

If the console database is unavailable (for example on Render), `src/key_store.py` falls back to environment keys. `KEY_SOURCE=env` forces that behavior.

### Security controls

- Host header allowlist: `127.0.0.1`, `localhost`, `::1` only → 400 otherwise.
- Per-session CSRF token on every mutation; missing/invalid token → 403.
- Login throttle: 5 attempts / 60 s per IP.
- Coarse request throttle: 600 reads/min and 60 writes/min per session/IP.
- CSP: `default-src 'self'`, no inline scripts, no CDN, `frame-ancestors 'none'`.
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- Payment links accept only `http(s)` URLs. No card data is collected, stored, or transmitted by this app.
- Audit log for console actions (client create/update, key changes, billing changes, login).

---

## 6. Environment variables

Set these in Render (Settings → Environment) and in `.env` locally. `.env` is gitignored — never commit it.

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEYS` | yes* | Comma-separated Groq keys. Weighted round-robin; 429 cooldown, next key on failure |
| `SUPPORT_AGENT_GROQ_API_KEY` | fallback* | Legacy single-key variable; used if `GROQ_API_KEYS` is unset |
| `GROQ_MODEL` | optional | Groq model ID. Default `openai/gpt-oss-20b`; also editable in Settings |
| `KEY_SOURCE` | optional | Set to `env` to skip the encrypted store and use env keys only |
| `TRUST_PROXY` | optional | `1` on Render so `X-Forwarded-For` is trusted (also auto-enabled when `RENDER` is set). Empty locally so spoofed headers are ignored |
| `NOTIFY_FORMSPREE_URL` | optional | Formspree endpoint, e.g. `https://formspree.io/f/<id>`. Empty = no escalation email |
| `NOTIFY_EMAIL_TO` | yes | Address included in escalation emails |
| `PORT` | set by Render | Server port (defaults 8000) |
| `PUBLIC_BOT_URL` | optional | Public backend URL used in console widget snippets/health links |
| `RENDER_DEMO_URL` | optional | Render service URL shown on the console Settings page |
| `DATABASE_URL` | optional | Neon/Postgres connection string for live conversation reads |
| `LEMON_SQUEEZY_STORE_URL` | optional | Shown on the Billing page |
| `SAFEPAY_STORE_URL` | optional | Shown on the Billing page |
| `VIZARA_ADMIN_DB` | test only | Override the console SQLite path |
| `VIZARA_ADMIN_BACKUPS` | test only | Override the backup directory |
| `VIZARA_COOKIE_SECURE` | optional | `1` only if serving the console over HTTPS (not local) |
| `NOTIFY_SMTP_USER` | legacy | **Unused** — the notifier sends via Formspree, not SMTP |
| `NOTIFY_SMTP_APP_PASSWORD` | legacy | **Unused** |

\* At least one Groq key variable must exist, or the LLM layer cannot generate. With the local console database present, keys come from the encrypted pool and env keys are only an import source/fallback.

Groq free tier as of November 2026: **1,000 requests/day and 8,000 tokens/minute per key**. `top_k: 10` can consume 3–4k tokens per RAG call, so keep `top_k` at 6 or lower on free keys. Add paid keys or more keys to raise the ceiling; no code change is needed.

---

## 7. Local development

**Python 3.11 is required.** (3.12 has no prebuilt `chroma-hnswlib` wheel; the Docker image is pinned to `python:3.11-slim` for this reason.) The default `python` on this machine is 3.13 and does not have the dependencies — use `py -3.11`.

```powershell
cd "D:\wasif\vizara support bot"
py -3.11 -m pip install -r requirements.txt
copy .env.example .env      # then fill in real keys

# build / rebuild the vector index for a client
py -3.11 -c "import sys; sys.path.insert(0,'.'); from src.ingest import ingest; ingest('vizara')"

# run the API
py -3.11 src/server.py      # http://localhost:8000

# run the console (separate terminal)
start-dashboard.bat         # http://127.0.0.1:8787
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

`scripts/run_eval.py` uses a fresh session per case and passes the required `client_id`. Override the tenant with `EVAL_CLIENT_ID=acme`. Last local run: 18/20 passed; both failures were external quota (Groq 429) and passed on retry.

`scripts/debug_retrieval.py` prints the chunks and scores behind a query, which is the fastest way to tell whether a bad answer is a retrieval or an LLM problem.

### Console smoke test

A reusable 43-check test lives at:

```
C:\Users\ali\AppData\Local\Temp\opencode\vizara-test\smoke.py
```

Run it against a throwaway admin DB so the real one is never touched:

```powershell
$env:VIZARA_ADMIN_DB="C:\Users\ali\AppData\Local\Temp\opencode\vizara-test\admin.db"
$env:VIZARA_SECRET_KEY_PATH="C:\Users\ali\AppData\Local\Temp\opencode\vizara-test\.secret.key"
$env:VIZARA_ADMIN_BACKUPS="C:\Users\ali\AppData\Local\Temp\opencode\vizara-test\backups"
py -3.11 -m uvicorn admin.app:app --host 127.0.0.1 --port 8787 --no-access-log
# in another terminal
py -3.11 "C:\Users\ali\AppData\Local\Temp\opencode\vizara-test\smoke.py" "D:\wasif\vizara support bot"
```

It covers health, Host rejection, setup/login, CSP/headers, every page, CSRF rejection, plans/subscriptions/invoices/payment link/payment recording, unsafe URL rejection, onboarding validation, real onboarding + config/content/manifest creation, exports, and logout. Clean up `data/smoke-client`, `config/client_smoke-client.yaml`, and the `client_smoke-client` Chroma collection afterward (the script does this itself).

---

## 8. Deployment

### Backend — Render (Docker)

- Auto-deploys `master`.
- `Dockerfile`: Python 3.11-slim, installs `gcc/g++/patchelf`, `pip install`, clears the executable-stack flag on native libs (`patchelf --clear-execstack` — required because `onnxruntime==1.16.3` is the last version that runs without AVX-512, and Render refuses exec-stack binaries), then **runs ingestion at build time** (`ingest('vizara')`) so the image ships with `chroma_db/`.
- Only `vizara` is ingested during the image build. At startup the stale-index check fills in any other configured client (`acme` has content; `test` has none). Missing collections still degrade gracefully (returns nothing → escalation).
- Health check: `/health` supports **GET and HEAD** (Render uses HEAD).
- Proxy headers: Render's edge adds `X-Forwarded-For`. The app trusts it automatically when `RENDER` is set; if that variable is absent, set `TRUST_PROXY=1` explicitly. Without it, every visitor shares the proxy IP and the per-visitor rate limit becomes a global limit.
- Image rebuild = fresh `chroma_db`. New/changed client content is picked up by the startup stale-index check, but a redeploy is still required to ship new raw files.

The Render free instance sleeps after 15 idle minutes, so the first request after a quiet period is slow. A free external monitor such as UptimeRobot can hit `/health` every 10 minutes to keep it warm. The free plan's 750 monthly instance-hours are the ceiling on this approach; a single always-warm service consumes nearly all of that allowance.

> **Action required:** the repository changes made for the console, registry, Groq-only pool, and widget are **not committed or pushed**. Render continues to serve the old code until you commit, push to `master`, and verify the deploy. The deployed widget is 10,319 bytes; the local widget is 11,623 bytes.

### Frontend — Netlify

- Auto-deploys `main`, publish directory is the repo root (plain static files).
- `chat-widget.js` is served by Netlify for the Vizara site; the canonical copy for new customers is served by the backend at `/widget/chat-widget.js`.

### Console — never deployed

The console stays on the operator PC. Its SQLite database and encryption key are local files. Do not add it to Render, Netlify, or any public host.

---

## 9. Billing and payments (summary)

Full runbook: **`PAYMENTS.md`**.

- **Recommended international provider:** Paddle (merchant of record; software only; global tax handled; pays out to Pakistan). Fees: **5% + 50¢** per transaction, with no separate international or subscription surcharge.
- **Fallbacks:** Polar and Dodo Payments, both merchants of record on comparable
  percentage-plus-fixed-fee terms — confirm their current rates and Pakistan payout rail
  before committing.
- **Domestic Pakistan option:** Safepay (2.9% + Rs 30 domestic, +0.3% international).
- **Lemon Squeezy is out:** it closed to new stores on 16 Nov 2026 and stops processing on 31 Mar 2027. Any existing store must be migrated before that date.
- **Stripe is not available** for Pakistan-based businesses.
- **v1 workflow:** create a hosted checkout/payment link at the provider, paste it into the console invoice, send it to the client, then mark the invoice paid when the money arrives. Recording payment extends the subscription one period.
- **No card data** is collected or stored. The console only stores the link URL, provider name, amount, and status.
- Automated webhooks/checkout creation are a follow-up once a provider account is approved. The data model already has payment links, invoices, subscriptions, plans, and status fields.

---

## 10. Security posture (what was audited)

Full report with findings and residual risks: **`SECURITY_AUDIT.md`**.

- `.env` is gitignored and never committed; git history was searched for leaked keys — clean.
- No secrets hardcoded in source.
- Groq keys are encrypted at rest (Fernet), masked in the UI, excluded from exports, and never logged. `admin/admin.db`, `admin/.secret.key`, WAL files, and `admin/backups/` are gitignored.
- `client_id` is validated against the config-driven registry before any filesystem/DB use — path traversal, slashes, and SQLi-style values are rejected with 403.
- Origin lockdown per client (browser-embedded widgets always send `Origin`). Requests with **no** Origin are allowed to keep curl/local dev usable — tighten this if you want strict production behavior.
- Per-client/per-visitor rate limit: 10 requests / 60 s, plus a 120 requests / 60 s client-wide ceiling (HTTP 429).
- Chat request bodies are bounded (`query` ≤ 2000 chars, `session_id` ≤ 128, `client_id` ≤ 64).
- Errors do not leak stack traces or file paths. The origin error echoes only the caller-supplied origin and client_id.
- Prompt-injection probes ("reveal your system prompt", cross-client questions) escalated without leaking prompts or other clients' data.
- Console: scrypt password hashing, server-side sessions, CSRF on mutations, login throttle, Host allowlist, CSP, frame denial, no CDN, no inline scripts.
- Console smoke test verifies Host rejection, CSRF rejection, security headers, unsafe payment URL rejection, and session required after logout.
- `client_secret_*.apps.googleusercontent.com.json` is **not** part of this project and is gitignored. Keep it that way.

**On handover, rotate:** Groq keys, the Formspree endpoint (or its form), Render account access, Netlify account access, and GitHub repo ownership. Transfer `.env` values out-of-band (password manager), never through git.

---

## 11. Known issues / limitations

1. **Render is running stale code.** Nothing in this session was committed or pushed. The live service does not yet have the registry, Groq-only pool, current widget, or console-related fixes. Deploy before relying on the live demo.
2. **Render free tier is a demo, not production.** It spins down when idle (cold start ~50 s), has ephemeral storage, and is not suitable for paying clients. Conversation logs disappear on redeploy. Move to a paid instance or attach a persistent disk before selling.
3. **Groq free-tier limits.** 1,000 requests/day and 8,000 tokens/minute per key. A 429 cools the key down and retries the next one; when all keys are exhausted, the bot escalates with the generic "share your email" reply. Add keys or upgrade for volume.
4. **No automated payment webhooks yet.** Invoices are marked paid manually after the provider confirms the transfer. Hosted links are pasted into the console. This is intentional for v1; see `PAYMENTS.md`.
5. **Escalation email needs `NOTIFY_FORMSPREE_URL`.** If unset (or the Formspree form is deleted), escalation emails silently fail and are only logged.
6. **Single worker only** — see §2.
7. **In-memory sessions** — history is lost on restart; no cross-instance sharing.
8. **Origin check allows missing Origin** by design; a non-browser attacker can omit it. Rate limiting and the client_id registry are the practical controls.
9. **Rate limit is per process**, so it resets on restart. It is enforced per `client_id` + visitor IP (10/min) plus a client-wide ceiling (120/min).
10. **Widget branding is attribute-driven, not config-driven.** `data-brand-name`, `data-greeting`, `data-followup-name`, and `data-header-sub` can be set on the embed tag; the backend does not inject tenant config into the JS bundle.
11. **Live Postgres reads are untested locally.** `DATABASE_URL` support exists in `metrics.py`, but `psycopg` is not installed and no Neon database was available. Install `psycopg[binary]` and test before relying on it.
12. **Content quality limits answers.** If a fact is not in the client's markdown (or the index is stale), the bot escalates instead of inventing. Reindex after content changes.
13. **The console is single-operator.** There is one password and no user roles. Keep it on a trusted machine.

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `403 Unknown or disabled client_id` | No `config/client_{id}.yaml`, or `enabled: false` → create/enable it in the console (picked up in ≤2 s) |
| `403 Origin '...' not allowed` | Request origin ≠ the client's `domain` → fix the YAML or the embed URL |
| `429` | Per-client/per-IP 10 req/min (or client-wide 120/min) limit hit → wait or raise `RATE_LIMIT` / `GLOBAL_RATE_LIMIT` |
| `500` on chat | Check Render logs. Missing collection is handled; other exceptions are not |
| "I don't have that information — let me connect you with a human." | No chunk above `score_threshold` → improve content or lower threshold |
| Correct policy answer but a price/details question escalates | Check `data/<name>/processed/manifest.json` and `[boot]` logs. Startup re-ingests changed sources automatically; force it with `ingest('<client_id>')` or the console Reindex button |
| All answers escalate / generic "share your email" reply | Groq keys invalid, model retired, or quota exhausted — check `[llm]` logs, verify the model at `https://api.groq.com/openai/v1/models`, change it in Settings or `GROQ_MODEL` |
| Console shows no keys | First-run import skipped or env keys absent → add a key on **API keys**; check `admin/.secret.key` exists |
| Console login loop / 400 Bad Request | You are not on `127.0.0.1`/`localhost`, or a proxy changed the Host header → use `http://127.0.0.1:8787` directly |
| Console 429 | Coarse throttle hit (600 reads/min, 60 writes/min) → wait a minute |
| `ModuleNotFoundError: chromadb` | Wrong interpreter — use Python 3.11 |
| `ImportError: cannot import name '_ExtendedAttributes'` | OpenTelemetry SDK/API mismatch. Run `py -3.11 -m pip install -r requirements.txt`; the pins keep the SDK and instrumentation aligned with FastAPI |
| Build fails on `chroma-hnswlib` | Must build on Python 3.11 (cp311 wheel) |
| Render crashes with exec-stack / SIGILL | Keep `patchelf --clear-execstack` + `onnxruntime==1.16.3` pin |
| Escalation email not received | Check `NOTIFY_FORMSPREE_URL`, `NOTIFY_EMAIL_TO`, the Formspree form status, and Render logs |
| Console page 500 after a dict change | Jinja resolves `dict.keys`/`dict.items` before your fields — never name context fields `keys` or `items` (the pool uses `key_list`) |

Useful log lines: `[llm] attempt N: Groq - <label>`, `[llm] all Groq keys exhausted`, `[mem] RSS=...`, `[notifier] ...`, `[boot] ...`, `[registry] ...`.

---

## 13. Product / pricing context

The frontend markets the chatbot service at **$90/mo + $150 one-time setup** (card in `index.html` → `chatBotPlans`). Cost side is dominated by Render (see dashboard) plus Groq usage. The service was designed to be cheap enough for local businesses (barbershops, studios) while sharing one backend.

Suggested first paid plan in the console is seeded as **Chatbot Starter — $90/month + $150 setup**. Change it on the Billing page; no code change is needed.

---

## 14. Handover checklist

- [ ] Commit and push the current backend/frontend changes; confirm Render + Netlify deploy
- [ ] Transfer GitHub repos `Wsif123/vizara-support-agent` and `Wsif123/vizarastudio`
- [ ] Transfer / re-link Render service and Netlify site
- [ ] Transfer domain / DNS (if custom domains are added later)
- [ ] Deliver `.env` values out-of-band (password manager) — never through git
- [ ] Rotate all API keys and create your own Formspree form after transfer
- [ ] Set `NOTIFY_FORMSPREE_URL` + `NOTIFY_EMAIL_TO` in Render
- [ ] Confirm `GROQ_MODEL` is live at `https://api.groq.com/openai/v1/models`
- [ ] Verify `/health` returns 200 (GET + HEAD)
- [ ] Verify a live chat message on https://vizarastudioo.netlify.app returns a grounded answer (not the generic escalation)
- [ ] Verify escalation email arrives
- [ ] Start the console with `start-dashboard.bat`, create the operator password, confirm both Groq keys show active
- [ ] Run the console smoke test against a throwaway DB (see §7)
- [ ] Create/verify a payment link and mark a test invoice paid
- [ ] Back up `admin/admin.db` after onboarding/billing changes
- [ ] Read `PAYMENTS.md` before taking real money
