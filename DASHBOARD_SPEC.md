# Vizara Operator Dashboard — Design Spec (v1)

Status: **built + smoke-tested (43/43)** · Updated: 2026-11-13 · Scope: local-only operator console

---

## 1. Purpose

A private back-office app for the operator (you) to run the Vizara support-bot SaaS without
editing code or reading logs by hand. It is **not** customer-facing and is **not deployed**.
It runs on your PC, opens in your browser, and binds only to `127.0.0.1`.

It answers four questions at a glance:

1. Who are my clients, and who is actually using the bot?
2. What are customers asking, and what is escalating?
3. Who has paid, who owes money, and when is the next invoice due?
4. Are my LLM API keys healthy, and how is load distributed across them?

---

## 2. External checks (verified 2026-11-12)

### Render

| Check | Result |
|---|---|
| `GET /health` | **200**, ~1.25 s |
| `GET /widget/chat-widget.js` | **200**, ~0.87 s |
| Frontend `vizarastudioo.netlify.app` | **200** |

Render is alive. It is serving the **old uncommitted code** (widget is 10,319 bytes; local is
11,623 bytes), so the bug fixes are not live until you push.

A live chat probe confirms the production LLM keys work: video-ad pricing and turnaround
answer correctly. The chatbot price question still escalates because the live index predates
`chatbot-service.md` — the stale-index fix in the local working tree solves it. This is why
the console surfaces index/manifest information on the Content tab.

**Recommendation: keep Render free for the public demo, do not migrate yet.** Caveats:

- Free instances spin down after inactivity; the next visitor can wait 30–60 s.
- Disk is ephemeral: `logs/conversations.db` and `chroma_db/` vanish on restart/redeploy.
- Not suitable for a paying client until it has persistent storage and no cold starts.

When the first paying client arrives, options are Render Starter + persistent disk (paid) or a
small VPS + free Neon Postgres. Neither is needed today.

### Groq

| Key | `/models` | `openai/gpt-oss-20b` | Chat probe | Free-tier headers |
|---|---|---|---|---|
| Key 1 (`gsk_…WEoH`) | 200 | present | 200, correct answer | 1,000 req/day · 8,000 tokens/min |
| Key 2 (`gsk_…Oawr`) | 200 | present | 200, correct answer | 1,000 req/day · 8,000 tokens/min |

- Both keys work. `openai/gpt-oss-20b` is present and answers correctly.
- `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` are **absent** from these keys' model
  lists — the old defaults are gone, as suspected.
- The model is a reasoning model; reasoning tokens count against the 8k TPM budget.
- **Capacity warning:** a RAG call with `top_k: 10` can use roughly 3–4k tokens. Two keys at
  8k TPM each support only a few concurrent requests per minute. The console surfaces TPM
  headroom and 429s, and `top_k` is tunable per client.
- A live generation through the encrypted console store was verified (`Hello`, 170 tokens,
  ~1.4 s), with the usage row written to `key_usage`.

### Cerebras

Removed. The provider, key, model, fallback code, and docs references are gone. Generation is
Groq-only by design; if every Groq key fails, the bot escalates instead of calling another
provider.

---

## 3. Decisions for v1 (as built)

| Area | Decision |
|---|---|
| Hosting | Local only, `127.0.0.1:8787`, never `0.0.0.0` |
| Launcher | `start-dashboard.bat` → starts server, opens browser, prefers `py -3.11` |
| Stack | Python 3.11 + FastAPI + Jinja2 + SQLite + vanilla JS/SVG |
| Build step | None. No Node, no CDN, no external fonts/assets |
| Auth | One operator password, server-side sessions, CSRF, login rate limit |
| Billing | Manual subscription + invoice tracker + hosted payment links |
| Payment provider | Lemon Squeezy (international, recommended) / Safepay (Pakistan, optional) |
| API keys | Groq only; encrypted at rest, masked, weighted round-robin, per-key usage |
| Onboarding | Built into the console: YAML + content + ingest + client record |
| Data source | Local `logs/conversations.db`; optional Neon/Postgres via `DATABASE_URL` |
| Production data | Optional; local SQLite fallback. Render keys fall back to env. |
| Theme | OpenCode-inspired dark console, single amber accent |

### Live data (optional, not required)

`admin/metrics.py` reads the bot's conversation table from `logs/conversations.db` by default.
If `DATABASE_URL` is set and `psycopg` is installed, it reads the same-shaped table from
Postgres instead — useful when Render writes to Neon. The dashboard stays local; only the data
source changes. This path is implemented but **not yet tested against a real Neon instance**;
`psycopg` is not installed locally.

---

## 4. Architecture

```
Browser (your PC)
  │  http://127.0.0.1:8787
  ▼
admin/app.py  (FastAPI, 1 process, 127.0.0.1 only)
  ├── auth.py          password, sessions, CSRF, audit, login throttle
  ├── security.py      host check, headers, coarse rate limits
  ├── db.py            SQLite, migrations, backups
  ├── metrics.py       usage aggregations (SQLite or Postgres)
  ├── billing.py       plans, subscriptions, invoices, payment links, MRR
  ├── keys.py          encrypted Groq pool, validation, usage summary
  ├── onboarding.py    client create/edit, content save, ingest, reindex
  └── templates/       Jinja2 pages (autoescaped)
        │
        ├── admin/admin.db          clients, billing, keys, sessions, audit
        └── logs/conversations.db   read-only chat history (or DATABASE_URL)
```

The bot and dashboard share code only through `src/key_store.py`. When the bot runs locally,
it reads active keys from `admin/admin.db` and records usage there. On Render it falls back to
`GROQ_API_KEYS` from env, because the dashboard DB is not present. No admin API is exposed by
the public bot.

### Folder layout

```
admin/
  app.py
  auth.py
  security.py
  db.py
  metrics.py
  billing.py
  keys.py
  onboarding.py
  migrations/001_init.sql
  templates/…
  static/app.css, app.js
  mockup.html
start-dashboard.bat
DASHBOARD_SPEC.md
PAYMENTS.md
```

---

## 5. Data model

All admin tables live in `admin/admin.db` (SQLite, WAL). Chat history is read from
`logs/conversations.db` with a read-only connection; the bot's schema is never modified.

### clients

| column | type | notes |
|---|---|---|
| id | TEXT PK | slug, must match bot `client_id` |
| display_name | TEXT | e.g. "Vizara Studio" |
| domain | TEXT | allowed origin domain |
| status | TEXT | `active` / `paused` / `archived` |
| contact_name | TEXT | escalation contact |
| contact_email | TEXT | operator-facing only |
| notes | TEXT | free text |
| created_at / updated_at | TEXT | ISO-8601 UTC |

### plans

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT | "Chatbot Starter" |
| amount_cents | INTEGER | 9000 = $90.00 |
| currency | TEXT | USD |
| interval | TEXT | `month` |
| setup_fee_cents | INTEGER | 15000 = $150.00 |
| active | INTEGER | 0/1 |

Seed: **Chatbot Starter — $90/month + $150 one-time setup** (matches `chatbot-service.md`).

### subscriptions

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| client_id | TEXT FK | |
| plan_id | INTEGER FK | |
| status | TEXT | `trial` / `active` / `past_due` / `canceled` |
| started_on | TEXT | date |
| current_period_end | TEXT | date; next renewal |
| cancel_at | TEXT | optional |
| created_at / updated_at | TEXT | |

### invoices

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| client_id | TEXT FK | |
| subscription_id | INTEGER FK | nullable for setup fees |
| number | TEXT UNIQUE | `INV-2026-0001` |
| issued_on / due_on | TEXT | dates |
| amount_cents | INTEGER | |
| currency | TEXT | USD |
| status | TEXT | `draft` / `sent` / `paid` / `void` |
| paid_on | TEXT | nullable |
| method | TEXT | `manual` / `bank` / `wise` / `lemonsqueezy` / `safepay` / `other` |
| notes | TEXT | |
| created_at | TEXT | |

**No card data is ever stored.** This is a ledger, not a payment processor.

### payment_links

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| client_id | TEXT FK | |
| invoice_id | INTEGER FK | nullable |
| provider | TEXT | `lemonsqueezy` / `safepay` / `manual` |
| kind | TEXT | `checkout` / `payment` / `subscription` |
| label | TEXT | operator description |
| url | TEXT | validated `http(s)` only |
| amount_cents | INTEGER | optional |
| currency | TEXT | USD |
| status | TEXT | `active` / `used` / `void` |
| created_at | TEXT | |

### api_keys

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| provider | TEXT | `groq` |
| label | TEXT | "Groq - main" |
| ciphertext | TEXT | Fernet-encrypted key |
| hint | TEXT | last 4 chars, for display |
| status | TEXT | `active` / `disabled` / `invalid` / `cooldown` |
| weight | INTEGER | 1–10, default 1 |
| cooldown_until | TEXT | set on 429 |
| last_checked_at / last_used_at | TEXT | |
| created_at / updated_at | TEXT | |

### key_usage

| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| key_id | INTEGER FK | |
| ts | TEXT | |
| model | TEXT | |
| status | TEXT | `success` / `429` / `error` |
| http_status | INTEGER | |
| prompt_tokens / completion_tokens / total_tokens | INTEGER | from provider `usage` |
| latency_ms | INTEGER | |
| error_code | TEXT | no secrets |

### admin_users, admin_sessions, audit_log

- `admin_users`: one row, `password_hash` = scrypt (N=16384, r=8, p=1, 16-byte salt).
- `admin_sessions`: SHA-256 hash of a random 32-byte token, `csrf_token`, `expires_at`,
  `last_seen_at`, flash fields. Cookie is `HttpOnly; SameSite=Strict`; 12 h idle / 7 d absolute.
- `audit_log`: ts, actor, action, target, ip, details (secrets redacted). Every mutation.

### conversation_notes

Escalation resolution without writing to the bot DB:

| column | type | notes |
|---|---|---|
| conversation_id | INTEGER | bot DB row id |
| client_id | TEXT | |
| resolved_at | TEXT | |
| resolved_by | TEXT | |
| note | TEXT | |

### settings

Key/value for non-secret settings: model ID, notification email, data paths, theme. Secrets
stay in `.env` or the encrypted key table.

---

## 6. Screens (as built)

Left sidebar, persistent. Top bar: page title, global search, key-pool health, command palette.
Content max-width 1400px, 24px gutters.

### 6.1 Overview

Four stat cards: active clients, messages in range, escalation rate, MRR. Then:

- Messages-per-day SVG chart (no external library).
- Groq key health card with per-key load bars.
- Client leaderboard with last activity.
- Recent escalations and system strip (data source, Render health if configured, DB size,
  last backup).

### 6.2 Clients

Searchable table: `Client · Domain · Status · Plan · Billing · Messages · Last active · ⋯`.

Actions: open, pause/resume, copy widget snippet, open bot URL. **New client** opens the real
onboarding form — not a checklist. It creates the record, writes `config/client_{id}.yaml`,
saves knowledge to `data/{id}/raw/`, runs ingestion, and optionally creates a subscription.

### 6.3 Client detail

Tabs: **Overview · Conversations · Billing · Content · Settings**

- Overview: usage chart, sessions, escalation rate, top questions, last active, widget snippet.
- Conversations: transcripts with escalation badges, search, export CSV, resolve notes.
- Billing: current subscription, invoices, payment links, record payment, next due.
- Content: source files, chunk count, last ingest, manifest state, “Reindex”, content upload.
- Settings: display name, domain, contact, retrieval `top_k` / `score_threshold`, pause.

### 6.4 Conversations

Filters: client, date range, escalated only, free-text search. Table + transcript drawer.
Actions: mark resolved (writes `conversation_notes`), copy, export CSV. Read-only for customer
messages, and `source` shows whether the data came from SQLite or Postgres.

### 6.5 Billing

Top cards: MRR, collected this month, outstanding, overdue count.

Table of subscriptions: `Client · Plan · Amount · Status · Period end · Next invoice · ⋯`.
Invoice table with status chips. “Record payment” modal sets `paid_on`, `method`, and extends
`current_period_end` by one interval. “Generate invoice” uses the next number in sequence.
Payment-link panel supports Lemon Squeezy, Safepay, or manual; URLs are validated.

Banner: **Hosted payment links + manual reconciliation — no card data is collected or stored.**

### 6.6 API Keys

Groq-only pool. Per key:

`Label · masked key · status dot · weight · requests today · tokens today · 429s · last used · test`

- Add key: paste once, validate against Groq before saving. The full key is never returned
  again — only `gsk_…WEoH`.
- Test: `/models` plus a 1-token chat probe; updates status/last checked.
- Enable/disable, weight 1–10, delete with typed confirmation.
- Cooldown countdown for 429s; red “invalid” state for 401/403.
- Capacity card: TPM headroom per key, estimated requests/min, 429 trend.
- Recent key events table: time, key, model, status, HTTP, tokens, latency, error code.
- “Test all” and “Copy env line” (for Render) buttons.

### 6.7 Settings

Admin password change, logout-all, data source status, Render health, notification email,
Groq model ID, key count, local file sizes, backups, about/version. No secrets are rendered.
Danger zone: change password (requires current password), sign out other sessions, backup now.

### 6.8 Auth

`/setup` on first run (minimum 12 characters) and `/login` afterward. No default password.
Login errors are generic; attempts are throttled.

---

## 7. Design system (OpenCode-inspired)

- **Canvas:** near-black `#0A0A0B`; panels `#101113`; raised `#15171A`.
- **Borders:** 1px `#22252A`; no drop shadows except modals.
- **Text:** `#E8E8E8`; muted `#8B9098`; faint `#5D636B`.
- **Accent:** amber `#F2A33C` (single accent; used for focus, active nav, primary buttons).
- **Status:** green `#4ADE80`, red `#F87171`, blue `#60A5FA`, yellow `#FBBF24`.
- **Type:** system UI stack for text; `ui-monospace` for numbers, IDs, keys, timestamps.
- **Scale:** 13px base, 12px table text, 20/24px page titles. Compact density.
- **Shape:** 8px radius panels, 6px inputs/buttons. 1px borders, no gradients.
- **Layout:** 224px sidebar, 56px top bar, sticky table headers, 24px gutters.
- **Charts:** hand-rolled SVG; 1.5px lines, no grid clutter, hover tooltip.
- **Motion:** 120ms ease; respect `prefers-reduced-motion`.
- **Empty states:** one sentence + one action, no illustrations.
- **Keyboard:** `Ctrl+K` command palette, `/` search, `Esc` close. Focus rings use the accent.

No external fonts, icons, or CDNs — everything is local and offline-friendly.

---

## 8. API key manager logic

1. **Resolution order** in `src/key_store.py`:
   - If `admin/admin.db` exists and `KEY_SOURCE` is not `env`: decrypt active keys from DB.
   - Else read `GROQ_API_KEYS` (comma-separated) / `SUPPORT_AGENT_GROQ_API_KEY` from env.
2. **Selection:** weighted round-robin over `status = active` and not in cooldown.
3. **On 429:** set `cooldown_until` from `Retry-After` (fallback 60 s), record usage, try next.
4. **On 401/403:** set `status = invalid`, record, try next, surface a red alert.
5. **On 5xx/timeout:** short cooldown (15 s), record, try next.
6. **On success:** record tokens/latency, clear cooldown, update `last_used_at`.
7. **Capacity:** dashboard computes requests/min from recent usage and shows TPM headroom
   using provider headers (`x-ratelimit-*`) captured on the last call.
8. **No fallback provider.** If every key is unavailable, generation returns an error and the
   agent escalates.

The full key is decrypted only in memory at request time. It is never rendered, logged, put in
an exception message, or returned by an API. The UI shows the hint only. Exports exclude keys
entirely.

---

## 9. Security model

| Control | Implementation |
|---|---|
| Network exposure | bind `127.0.0.1`; reject non-local `Host` headers (DNS rebinding) |
| Auth | scrypt password hash; first-run setup; no default password |
| Sessions | 32-byte random token; SHA-256 stored server-side; HttpOnly; SameSite=Strict; 12 h idle / 7 d absolute |
| CSRF | per-session token on every state-changing request |
| Rate limits | login 5/min/IP; writes 60/min; reads 600/min per session/IP |
| Headers | CSP `default-src 'self'`; `frame-ancestors 'none'`; nosniff; DENY frames; no-referrer; no-store |
| SQL | parameterized queries only; no string-built SQL |
| XSS | Jinja autoescape; `textContent` in JS; no `innerHTML` with user data |
| Paths | client IDs validated against `^[a-z0-9_-]{1,64}$`; content paths confined to `data/<id>/raw` |
| Secrets | keys encrypted at rest (Fernet); master key in `admin/.secret.key` (gitignored); `.env` gitignored |
| Payment links | `http`/`https` only; `javascript:` and malformed URLs rejected |
| Logs | no request bodies, no secrets, no full keys; audit log redacts |
| Backups | console snapshot button; DB files never committed |
| Dependencies | `pip-audit` review before adding packages |

End-of-build security audit: auth bypass, CSRF, XSS, SQLi, path traversal, secret exposure,
rate-limit behavior, cookie flags, Host header, error messages, dependency CVEs.

---

## 10. Billing workflow (v1, free)

- Manual ledger + hosted payment links. No card data, no webhooks yet.
- Default plan: **$90/month + $150 setup**, USD, matching the chatbot service content.
- Actions: create plan, create subscription, generate invoice, attach hosted payment link,
  mark sent/paid/void, record method/date, extend period by one interval, add notes.
- Derived numbers: MRR = sum of active monthly plans; collected = paid invoices this month;
  outstanding = sent but unpaid; overdue = due date passed.
- Export CSV for accounting.
- When a provider account exists, create the checkout/subscription link at the provider, paste
  it into the invoice, and send it. After payment, mark the invoice paid. Automated webhooks
  are a later addition; the data model already carries provider, link, and status.
- See **`PAYMENTS.md`** for provider comparison, fees, Pakistan payout notes, and the setup
  runbook.

---

## 11. Build status

| Phase | Status |
|---|---|
| Foundation — DB, migrations, auth, middleware, base layout, launcher | Done |
| Overview + Clients — metrics, leaderboard, client CRUD, detail | Done |
| Conversations — filters, transcripts, resolve notes, CSV | Done |
| Billing — plans, subscriptions, invoices, payment links, MRR | Done |
| API keys — encrypted Groq store, validation, pool, usage, events | Done |
| Onboarding — YAML, content, ingest, record, reindex | Done |
| Settings + polish — backups, exports, empty states, palette | Done |
| Security audit — headers, CSRF, Host, secrets, URL validation | Done |

Acceptance: launches with one double-click, no internet required, no secret visible in UI or
logs, all mutations audited, and the security checklist passes. The 43-check smoke suite
covers health, Host rejection, setup/login, CSP, all pages, CSRF rejection, billing flow,
payment-link validation, onboarding, exports, and logout.

---

## 12. Explicitly out of scope for v1

- Hosting the dashboard anywhere.
- Customer-facing accounts or a client portal.
- Real card charging / Stripe / Paddle / automated webhooks.
- Multi-user roles or 2FA (single operator password).
- Automatic payment reconciliation from provider APIs (manual for now).
- Editing or deleting the bot's conversation log.
- A fully tested live Postgres path (implemented, needs a Neon instance + `psycopg`).

---

## 13. Open questions (post-v1)

1. **Live data:** add Neon now, or keep local SQLite until Render gets persistent storage?
2. **Provider:** Lemon Squeezy for international first, or Safepay for Pakistan first?
3. **Plan:** keep $90/month + $150 setup, or introduce tiers?
4. **Auth:** stay single-operator, or add a second read-only login for support staff?
