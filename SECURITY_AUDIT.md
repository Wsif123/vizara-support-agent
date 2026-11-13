# SECURITY AUDIT — Vizara Support Agent + Operator Console

**Date:** 13 November 2026
**Scope:** public FastAPI bot (`src/`), embeddable widget (`widget/`), local operator console
(`admin/`), build/deployment files, dependency pins.
**Method:** manual code review, targeted greps, secret-pattern scan, `.gitignore` /
`.dockerignore` verification, `pip-audit`, and the 43-check console smoke test.

This was a pre-production review of a prototype, not a formal penetration test.

---

## 1. Result

**No critical or high-severity issue remains open in the application code.**

Five issues were found and fixed during the audit:

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | `.dockerignore` did not exclude `admin/`, so a local `admin/admin.db` and `admin/.secret.key` could be baked into the public Render image | `admin/`, `scratch pad.txt`, client secrets, and `.env.*` now excluded |
| 2 | Medium | Export CSVs passed customer-supplied text straight to spreadsheets (formula injection: `=`, `+`, `-`, `@`) | `_csv_safe()` prefixes dangerous leading characters with `'` |
| 3 | Medium | Public bot trusted the **first** `X-Forwarded-For` entry, which a caller can spoof to bypass per-IP rate limits | Forwarding headers are trusted only when `TRUST_PROXY=1`/`RENDER` is set, and only the right-most proxy-added address is used; parsed with `ipaddress` |
| 4 | Low | Public CORS used `allow_origins=["*"]` together with `allow_credentials=True` | `allow_credentials=False`; endpoint-level Origin allowlisting remains the real control |
| 5 | Medium | Pinned dependencies had known CVEs (`starlette`, `cryptography`, `python-multipart`, `python-dotenv`) | Pins upgraded and regression-tested (see §5) |

Two operational items need action from the owner:

- A **full Groq key is present in the gitignored `scratch pad.txt`**. It is not tracked by git
  and is excluded from the Docker image, but it exists in plaintext on disk. Rotate it in the
  Groq console if that file was ever shared, synced, or copied.
- The **Render deployment was running older, uncommitted code** at audit time. Push and
  redeploy to put these fixes live.

---

## 2. Secrets and repository hygiene

| Check | Result |
|---|---|
| `.env` tracked by git | No — ignored (`.gitignore:2`) |
| `admin/admin.db`, WAL/SHM files | Ignored. A real local console DB exists (operator user, two **encrypted** Groq keys, three clients); it contains no plaintext keys |
| `admin/.secret.key` | Ignored. Exists locally (required to decrypt the key pool); never leaves the machine |
| `admin/backups/` | Ignored |
| Full API keys in tracked source | None found |
| Full API keys anywhere on disk | One in gitignored `scratch pad.txt` (see above) |
| Keys in Docker image | Prevented after the `.dockerignore` fix; `.env`, `admin/`, and `scratch pad.txt` are excluded |
| Keys in console UI / exports / logs | Masked (`gsk_…XXXX`), excluded from CSV exports, never logged |
| Key storage | Fernet-encrypted ciphertext in `api_keys`; master key in `admin/.secret.key` (32 random bytes); decrypted only in memory at request time |

The console imports `GROQ_API_KEYS` / `SUPPORT_AGENT_GROQ_API_KEY` once, then manages the
encrypted pool. On Render, where the console database is absent, `src/key_store.py` falls back
to environment keys by design.

---

## 3. Authentication and session security (console)

| Control | Implementation | Verified |
|---|---|---|
| Password hashing | scrypt, N=16384, r=8, p=1, 16-byte random salt | Yes |
| Default password | None; first-run `/setup`, minimum 12 characters | Yes |
| Session storage | 32-byte random token; SHA-256 hash stored server-side | Yes |
| Cookie flags | `HttpOnly`, `SameSite=Strict`, `Path=/`; `Secure` opt-in via `VIZARA_COOKIE_SECURE=1` | Yes (Secure cannot be set on plain HTTP localhost) |
| Timeouts | 12 h idle, 7 d absolute, enforced on lookup | Yes |
| CSRF | Per-session token required on every state-changing request; 403 otherwise | Yes (smoke test) |
| Login throttling | 5 attempts / 60 s per IP | Yes |
| Coarse throttling | 600 reads/min, 60 writes/min per session/IP | Yes |
| Logout | Server-side session row deleted; other sessions can be revoked | Yes |
| Audit log | Login, client changes, billing changes, key changes, reindex, logout-all | Yes |

One operator, one password, no roles or 2FA. That is an accepted v1 limitation for a
local-only console on a trusted machine.

---

## 4. Application security

### 4.1 Network exposure and request validation

- Console binds `127.0.0.1:8787`; Host-header allowlist rejects anything except
  `127.0.0.1`, `localhost`, and `::1`, blocking DNS-rebinding attacks.
- Public bot binds `0.0.0.0` as intended for Render; it is the only internet-facing service.
- Console responses set CSP (`default-src 'self'`, `frame-ancestors 'none'`,
  `base-uri 'none'`, `form-action 'self'`), `X-Frame-Options: DENY`, `nosniff`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy`, and `Cache-Control: no-store`.
- No CDN, external font, inline script, or third-party asset is loaded by the console.

### 4.2 Injection

- **SQL:** all queries are parameterized. The only dynamic SQL fragments are generated `?`
  placeholder lists for `IN (...)` clauses and a fixed `WHERE` builder in `metrics.py`.
- **XSS:** Jinja autoescape is on; `admin/static/app.js` uses `textContent` and never assigns
  customer data through `innerHTML`. The widget's single `innerHTML` assignment is a static
  template (no interpolation of messages).
- **CSV injection:** fixed — `=`, `+`, `-`, `@`, tab, and CR at the start of a cell are
  prefixed with `'`.
- **Path traversal:** client IDs must match `^[a-z0-9][a-z0-9_-]{0,63}$`; upload filenames are
  basename-checked against `^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}$`; only `.md`, `.markdown`,
  and `.txt` are accepted; content paths resolve under `data/<client_id>/raw/`.
- **Command execution:** no `eval`, `exec`, `pickle`, `subprocess`, or `os.system` in
  request paths.
- **Payment links:** only absolute `http://` / `https://` URLs are stored; `javascript:` and
  relative URLs are rejected.

### 4.3 Public chat endpoint

- `client_id` is checked against the config-driven registry before any filesystem or database
  use; unknown/disabled clients get 403.
- Browser `Origin` must match the client's registered domain; mismatches get 403. Requests
  with no Origin are allowed so `curl` and local development still work (documented tradeoff).
- Request bounds: `query` ≤ 2000 chars, `session_id` ≤ 128, `client_id` ≤ 64.
- Per-visitor limit: 10 requests / 60 s keyed by `client_id` + IP; client-wide ceiling:
  120 requests / 60 s.
- Forwarded headers are ignored unless a trusted proxy is declared; the proxy-appended
  (right-most) address is used, not the client-controlled first entry.
- CORS has no credentials. Per-client Origin validation is enforced in the handler.
- Errors return short messages without stack traces or file paths.

### 4.4 File uploads and onboarding

- Per-file limit 2 MB, total 10 MB, pasted content 500k characters.
- Uploads are decoded as UTF-8 (BOM tolerated) with a cp1252 fallback and written with
  normalized newlines.
- YAML is written atomically via a temporary file and `os.replace`.
- Onboarding never edits `server.py`; it writes `config/client_<id>.yaml`, content under
  `data/<id>/raw/`, runs ingestion, and inserts the console record.

### 4.5 Data handling

- The console never collects or stores card numbers, CVVs, or bank credentials. Billing is a
  ledger plus hosted payment-link URLs.
- Conversation transcripts are read from the bot's SQLite database (or Postgres when
  `DATABASE_URL` is configured); the console does not modify customer messages.
- Exports contain client contact data and invoice data, not secrets or API keys.
- Backups are local SQLite snapshots under `admin/backups/` (gitignored).

---

## 5. Dependency audit

Tool: `pip-audit` against `requirements.txt`.

| Package | Before | After | Notes |
|---|---|---|---|
| fastapi | 0.115.0 | 0.162.5 | Pulls a patched Starlette |
| starlette | 0.38.6 (transitive) | 1.10.0 | Fixes request-smuggling / multipart / DoS advisories |
| cryptography | 48.0.0 | 50.0.2 | Fixes certificate-validation advisories |
| python-multipart | 0.0.27 | 0.0.33 | Fixes multipart parsing advisories |
| python-dotenv | 1.0.1 | 1.2.2 | Fixes symlink-following write advisory (not used in request paths) |
| chromadb | 0.5.3 | 0.5.3 (unchanged) | Three advisories with **no fixed version** — see below |

The FastAPI upgrade pulls a newer `opentelemetry-api`; `requirements.txt` pins the matching
SDK/instrumentation set (`1.45.2` / `0.66b2`). Without that alignment, Chroma fails to import
with `ImportError: cannot import name '_ExtendedAttributes'`.

`posthog` is pinned to `3.25.0` because Chroma 0.5.3 calls the older positional `capture()`
API. Telemetry is disabled (`anonymized_telemetry=False`); the pin removes repeated
`TypeError` log noise. `pip-audit` reports no known vulnerabilities for that version.

### Residual: ChromaDB advisories

`pip-audit` reports PYSEC-2026-3813/3814/3815 against `chromadb==0.5.3` with no fix versions.
They concern Chroma's **server mode** (multi-tenant authorization, authenticated collection
access, and `trust_remote_code` when loading a caller-supplied model repository).

This project does **not** run a Chroma server or expose Chroma's HTTP API. It uses
`chromadb.PersistentClient` against a local directory, with content supplied only by the
operator, and no remote model repositories. The practical exposure is therefore very low.
Recommended follow-up: evaluate a Chroma 1.x upgrade in a branch (index migration and
embedding compatibility must be tested before switching the Docker image).

---

## 6. Verification performed

- 43/43 console smoke checks passed against a throwaway database: health, Host rejection,
  setup/login, headers/CSP, every page, CSRF rejection, plans/subscriptions/invoices/payment
  link/payment recording, unsafe URL rejection, onboarding validation, real onboarding with
  config/content/manifest creation, CSV exports, logout.
- A live Groq generation through the encrypted key store returned a valid answer, recorded
  token usage, and wrote a `key_usage` row.
- Secret-pattern scan (Groq/OpenAI/Cerebras/Google key formats) found no keys in tracked or
  non-ignored source files.
- `git check-ignore` confirms `.env`, `admin/admin.db`, and `admin/.secret.key` are ignored.
- IP-handling unit checks confirm spoofed `X-Forwarded-For` is ignored locally and only the
  proxy-added entry is used when trust is enabled.
- Production Render `/health`, widget, and frontend returned 200; the deployed widget is older
  than the local build (redeploy required).

---

## 7. Residual risks and recommendations

1. **Local machine trust.** The console is only as secure as the PC it runs on. Use a
   password-protected OS account, full-disk encryption, and a private browser profile.
2. **Single operator, no 2FA.** Add a second factor (TOTP) if the console ever moves beyond
   one trusted machine.
3. **`scratch pad.txt` key.** Rotate the Groq key it contains and delete/move the file.
4. **Render free tier.** Ephemeral disk, cold starts, no persistent conversation logs; not
   suitable for paying customers. Move to a paid instance or VPS before selling.
5. **Rate limits are in-process.** They reset on restart and do not coordinate across
   workers. Keep one worker, or move counters to Redis before scaling.
6. **Missing-Origin requests are allowed** on `/chat`. Tighten this if non-browser clients
   become a problem; it is needed for `curl` and some server-side embeds.
7. **Live Postgres path is untested** against a real Neon instance; `psycopg` is optional.
8. **No payment webhooks.** Reconciliation is manual in v1; see `PAYMENTS.md` for the
   post-v1 receiver design.
9. **Chroma 0.5.3 advisories** — documented above; low exposure in the embedded, local-only
   configuration.
10. **Backups are unencrypted local snapshots.** Protect the folder or encrypt backups if
    they leave the machine.

---

## 8. Go-live security checklist

- [ ] Rotate all Groq keys; delete or move `scratch pad.txt`
- [ ] Commit and push the audited code; redeploy Render; confirm the new widget is served
- [ ] Set `TRUST_PROXY=1` on Render if `RENDER` is not present
- [ ] Set `NOTIFY_FORMSPREE_URL` and `NOTIFY_EMAIL_TO`; send a test escalation
- [ ] Start the console once, create a strong operator password, confirm both keys show active
- [ ] Run the 43-check smoke test against a throwaway DB
- [ ] Back up `admin/admin.db`; keep `admin/.secret.key` separately and securely
- [ ] Enable 2FA on Groq, Render, Netlify, GitHub, email, and payment accounts
- [ ] Read `PAYMENTS.md` before accepting the first payment
- [ ] Re-run `pip-audit -r requirements.txt` after any dependency change
