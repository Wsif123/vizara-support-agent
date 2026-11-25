# Onboarding runbook

One client, start to live. Do the steps in order; each one has a check that
must pass before the next. Steps 3–6 assume the setup fee has cleared — if it
has not, stop after step 2 and see step 7.

## 1. Discovery call

Get four answers on the call, in writing:

- **What the bot answers** — the topics that are in scope (pricing, hours,
  policies, product questions). Be specific: "order tracking" is not in scope,
  "what your return window is" is.
- **What must always escalate** — anything the client will not let a bot decide:
  refunds, complaints, contract terms, custom or negotiated pricing, and
  anything not covered by their content.
- **Who receives escalations** — named person and a mailbox several humans read.
  This goes in Settings as the escalation contact + email.
- **Their response-time promise** — what the widget may tell customers, e.g.
  "within ~1 hour". This is `sla_text`. Do not invent one; the client owns it.

## 2. Content collection

Ask for their **real FAQ and policy text** — the answers support staff already
send, not marketing copy. One or more `.md` files is ideal; pasted text is fine.

Always write a short **"Services / About overview"** section at the top of the
content, even if they did not send one: 3–6 plain sentences saying what the
business does, who it serves, and what it sells. Summary questions like "what do
you do?" retrieve badly without it — this was a real failure in testing, and it
also anchors every later answer.

```markdown
# Services overview

<Business> provides <what> for <who>, from <where>. We offer <service 1>,
<service 2> and <service 3>. <One sentence on how to buy or get started.>
```

Other rules: keep one topic per section, use the client's own numbers and
wording, and delete stale prices rather than leaving two versions in the file.

## 3. Console

Run `start-dashboard.bat`, then **Clients → New client**.

- Client ID: lowercase slug (`acme`, `northwind`). It is permanent.
- Display name: as the client writes it.
- **Allowed domain: leave blank for now.** It is set after the first deploy
  (step 6); a blank domain means no origin lock yet.
- Contact name / email: from discovery.
- Paste the content into the knowledge box, or upload the `.md` files.
- Submit. The console writes `config/client_<id>.yaml`, saves the content under
  `data/<id>/raw/`, and builds the index.

Check: the client page shows the expected files with **Present** status and a
chunk count above zero.

## 4. Persona

Persona is **tone only — 2–3 sentences**. No facts, no policies, no service
rules; content belongs in the knowledge files.

> You are a friendly, concise support agent for Acme Corp. Answer in the
> customer's language, stay close to the retrieved context, and hand off to a
> human rather than guessing.

Service-specific rules (which file wins for pricing, never mixing two services'
prices, forbidden claims) go in `prompt_rules` in `config/client_<id>.yaml`.
That field is not in the console; edit the YAML and promote again.

## 5. Test set

Write **at least 15 real questions** they actually get asked: about 10 the
content should answer, **3 off-topic** (must be politely declined, not
escalated), and **3 that must escalate**. Use the same shape as
`eval/eval_set.yaml`:

```yaml
- question: "What is your return window?"
  expect: answerable
- question: "Do you fix diesel engines?"
  expect: off_topic
- question: "Can I get a refund for last month?"
  expect: needs_human
```

Run them against that client before promoting — `run_eval.py` reads
`eval/eval_set.yaml`, so keep the client's cases there (or copy them in) while
you test:

```powershell
$env:EVAL_CLIENT_ID = "acme"
py -3.11 scripts/run_eval.py
```

Store the finished set as `eval/acme_eval_set.yaml` so replacing
`eval/eval_set.yaml` does not lose another client's questions.

Every failure is fixed in the **content**, not by loosening the test. Do not
promote while a case fails.

## 6. Promote

```powershell
py -3.11 scripts/promote_client.py <id>
```

The script refuses to run if anything outside `config/` and `data/` is dirty,
then commits, pushes, and triggers the Render deploy. Check `/health`, then send
one real message through the client page.

**Then set the domain:** console → client → **Settings → Allowed domain**, save.
Until this is set any origin can post; once set, only that domain can. Send one
more message from the client's real site to confirm the lock passes.

## 7. Invoice

**Billing** in the console: create the setup-fee invoice, add the payment link,
send it, and record the payment when it clears. Only then create the first
month's invoice.

**Never start before the setup fee clears.** No build, no promotion, no snippet
until the money is in — if it is unpaid, stop after step 2.

## 8. Handover

- Copy the **Widget snippet** from the client's Overview tab and send it with
  one line of install instructions (paste before `</body>`).
- Send a test escalation and **confirm the client can read it** at the contact
  email, including the customer's message.
- Confirm the client knows what the bot will and will not answer (from step 1).
- **48-hour check-in:** read their Conversations page together, fix anything
  wrong in content, and confirm the escalation mailbox is being watched.

## When it gets something wrong

Say what happened, then fix the cause — no guessing in the chat window.

1. **Conversations** page: open the exact conversation, find the question, and
   read the answer and escalation state. Quote the customer's wording back.
2. **Content** tab: if the fact is missing, wrong or stale, correct the source
   file and upload it (same filename replaces the old one) or press **Reindex**.
   Missing or vague "Services / About" text is the usual cause of bad summary
   answers.
3. If the right text was retrieved but the answer still went wrong, it is a
   persona or `prompt_rules` problem — tighten the rule, not the test.
4. Re-run the affected test cases, then promote again.

Reply to the client in plain terms: what the bot said, why, what changed, and
when it is live. Never blame the model; own the content gap.
