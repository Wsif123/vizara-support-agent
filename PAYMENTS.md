# PAYMENTS — Vizara Support Bot

How to take money for the chatbot service from Pakistan, without touching card data.

**Status:** v1 uses **hosted payment links + manual reconciliation**. Real charging starts as
soon as a provider account is approved. No card numbers, CVVs, or bank credentials are stored
in this repo or in the console.

> **Provider change (November 2026).** Lemon Squeezy closed to new stores on **16 Nov 2026**
> and stops processing on **31 Mar 2027**. It is no longer the recommendation; **Paddle** is.
> Any Lemon Squeezy store still in use must be migrated before 31 March 2027.

---

## 1. What v1 does — and what it does not

**Does:**

- Tracks plans, subscriptions, invoices, and payment status in the local console.
- Stores a hosted checkout/payment-link URL per invoice (Paddle, Polar, Dodo Payments,
  Safepay, or manual).
- Lets you mark an invoice paid and extends the subscription by one billing period.
- Exports invoices and clients to CSV for accounting.
- Keeps an audit trail of billing changes.

**Does not:**

- Charge cards or hold funds.
- Create checkout sessions through a provider API (you create the link in the provider dashboard).
- Receive webhooks (the console is local-only and has no public URL).
- Send invoice emails automatically (copy the link/invoice text and send it yourself).
- Handle refunds or disputes (do those in the provider dashboard, then update the ledger).

This is deliberate. It gets you paid professionally now, with zero card-data exposure, while
leaving room to automate later.

---

## 2. Provider comparison

| | **Paddle** (recommended) | **Polar** (fallback) | **Dodo Payments** (fallback) | **Safepay** (domestic) |
|---|---|---|---|---|
| Type | Merchant of record (MoR) | Merchant of record | Merchant of record | Payment gateway |
| Who handles sales tax/VAT | Paddle | Polar | Dodo | You |
| Best for | International SaaS, subscriptions, global tax | Indie software, simple MoR onboarding | Software with Asia-focused payouts | Pakistani cards, wallets, bank transfers |
| Pricing | 5% + 50¢ per transaction | ~4% + 40¢ (confirm current rate) | ~4% + 40¢ (confirm current rate) | ~2.9% + Rs 30 domestic; +0.3% international cards |
| Payouts to Pakistan | Yes | Confirm rail and eligibility before you commit | Offered; confirm merchant country and rail | To Pakistani business bank account |
| Subscriptions | Native | Native | Native | Supported; confirm current product limits |
| Hosted checkout links | Yes | Yes | Yes | Yes |
| Chargebacks | MoR handles/disputes | MoR handles | MoR handles | You handle |
| Setup difficulty | Medium (business review) | Low | Low–medium | Medium (business KYC, sandbox) |

**Recommendation:** **Paddle** for international clients. It is an MoR on standard terms,
accepts Pakistan-based sellers, pays out to Pakistani banks, and its **5% + 50¢** is
all-in — no separate international or subscription surcharge. Polar and Dodo Payments are
fallbacks if Paddle declines the store or its review drags; check their payout rail to
Pakistan before quoting a client, since that is the part that changes. Add **Safepay** when a
Pakistani client wants to pay in PKR through a local card/wallet/bank.

> **Paddle sells software only.** Its acceptable-use policy covers software and digital
> products, which fits the chatbot subscription. Do **not** route video-ad or product-photo
> work through Paddle — those are services. Keep the console plan price in USD and note the
> actual settlement currency.

> **Stripe is not available to Pakistan-based businesses.** Do not build the flow around it.
> PayPal receiving is also limited in Pakistan; do not promise PayPal checkout.

Fees and availability change. The numbers above are from research done in November 2026;
confirm the current schedule in each provider's dashboard before quoting a client.

---

## 3. Cost examples

Assumes the **Starter** plan: **$149/month + $500 one-time setup**. Growth ($249/$750) and
Scale ($399/$1,500) use exactly the same fee math.

### Paddle (international card, subscription)

| Charge | Calculation | Fee | Net |
|---|---|---|---|
| $149 monthly | 5% ($7.45) + $0.50 | **$7.95** | **$141.05** |
| $500 setup | 5% ($25.00) + $0.50 | **$25.50** | **$474.50** |
| $649 first payment (one combined charge) | 5% ($32.45) + $0.50 | **$32.95** | **$616.05** |

There is no extra international or subscription percentage on top. If you invoice the setup
fee and the first month as two separate charges, you pay the 50¢ twice ($33.45 combined).

### Safepay (Pakistan, illustrative FX at Rs 280/USD)

| Charge | Calculation | Fee (PKR) | Fee (USD) | Net (USD) |
|---|---|---|---|---|
| $149 ≈ Rs 41,720 | 2.9% + Rs 30 + 0.3% international | Rs 1,365 | ~$4.88 | ~$144.12 |
| $500 ≈ Rs 140,000 | 2.9% + Rs 30 + 0.3% international | Rs 4,510 | ~$16.11 | ~$483.89 |
| $649 ≈ Rs 181,720 | 2.9% + Rs 30 + 0.3% international | Rs 5,845 | ~$20.88 | ~$628.12 |

Domestic Pakistani cards skip the extra 0.3%. Safepay settles in PKR; the USD equivalent
depends on the FX rate and any conversion spread. Decide whether to quote Pakistani clients in
PKR or USD before you send the first link.

**Rule of thumb:** Paddle costs a little more but removes tax/subscription/chargeback
work. Safepay is cheaper domestically but leaves tax and disputes with you.

---

## 4. Paddle setup runbook

### 4.1 Create and verify the account

1. Go to <https://www.paddle.com> and create a seller account with a business email you
   control.
2. Verify the email and enable two-factor authentication.
3. Complete the business profile: legal entity/sole proprietor, country (Pakistan), payout
   currency, support email, and the website URL. Paddle reviews the store and the product
   before it goes live — expect questions about what the chatbot is and who buys it.
4. Add payout details (Pakistani bank account). Complete KYC/tax forms before expecting money.
5. Read the MoR terms so you can explain to clients that Paddle is the seller of record and
   handles sales tax on the transaction.

### 4.2 Create the products

Create one subscription product per tier, plus one setup product per tier (or a single
one-time setup product per tier if you prefer per-plan pricing):

| Product | Type | Price | Notes |
|---|---|---|---|
| Chatbot Starter | Subscription | $149 / month | 500 conversations/month |
| Chatbot Setup — Starter | One-time | $500 | Onboarding/build fee |
| Chatbot Growth | Subscription | $249 / month | 2,000 conversations/month |
| Chatbot Setup — Growth | One-time | $750 | |
| Chatbot Scale | Subscription | $399 / month | 6,000 conversations/month |
| Chatbot Setup — Scale | One-time | $1,500 | |

Optional, only if you do **not** also send the monthly subscription link for month one: a
combined first-payment product at plan price + setup ($649 / $999 / $1,899).

Paddle charges per transaction, so separate setup and monthly links cost one extra 50¢
compared with a single combined charge. For each product, add a short description and a clear
refund policy, and keep the name/price in sync with the console plan.

### 4.3 Get the checkout links

1. Open the product (or **Checkout** → **Payment links**) in the Paddle dashboard.
2. Copy the hosted checkout URL, or generate a payment link for the price.
3. Use the **sandbox** first. Run a test purchase with a test card, confirm the receipt and
   the transaction appears, then refund/cancel the test transaction.
4. Switch to live and copy the **live** link. Do not send a sandbox link to a client.

If you need the client's email prefilled, Paddle checkout links support query parameters
(for example `?customer[email]=...`). Keep it simple in v1: send the plain link.

### 4.4 Attach the link in the console

1. Open **Billing → New payment link**.
2. Choose provider **Other** — the dropdown still lists Lemon Squeezy and Safepay only — and
   put `paddle` in the link label so the ledger stays readable. Kind **checkout**, paste the
   live URL.
3. Optionally attach it to an invoice and set the amount.
4. The URL must start with `https://`. `javascript:` and malformed URLs are rejected.
5. Open **Billing → Invoices → Add link** if you prefer to attach it directly to an invoice.

### 4.5 Collect and reconcile

1. Send the client the invoice and the checkout link (email/WhatsApp/your normal channel).
2. The client pays on Paddle's page; they get a receipt from the provider.
3. When the transaction/settlement appears in the Paddle dashboard, open the invoice in the
   console and **Record payment**: method `other` with `paddle <transaction id>` in the note
   (the method list predates Paddle), plus the date.
4. Recording payment extends the linked subscription one period. Confirm the new period end.
5. If the client pays the setup fee and subscription separately, record each invoice/payment
   separately so the ledger matches the provider.

### 4.6 Monthly routine

- Export the Paddle transactions/payouts CSV.
- Match each payout to console invoices; investigate anything unpaid after 7 days.
- Back up `admin/admin.db` after billing changes.
- Keep provider payout statements with your income-tax records.

---

## 5. Safepay setup runbook (optional, Pakistan)

### 5.1 Create the business account

1. Go to <https://www.safepay.com> and create a business account.
2. Provide the business docs requested: company/sole-proprietor registration, NTN, CNIC,
   bank account, website, and contact details.
3. Enable two-factor authentication on the account email.
4. Complete sandbox onboarding first. Safepay provides test credentials and a sandbox
   dashboard.

### 5.2 Create a payment link (v1)

1. In the Safepay dashboard, create a payment link for the amount and currency (PKR or USD as
   supported by your account).
2. Add a clear description: "Vizara chatbot setup" or "Chatbot Starter — monthly".
3. Copy the hosted link.
4. Test it in sandbox with Safepay's test cards/wallets.
5. Create the live link and paste it into **Billing → New payment link** with provider
   **Safepay**.

For recurring billing, Safepay supports subscriptions/plans, but the exact setup depends on
your account and integration. If recurring links are not enabled, send a fresh payment link
each month and keep the console subscription on manual renewal.

### 5.3 Settle and reconcile

1. Confirm the payment in the Safepay dashboard.
2. Record the payment in the console with method `safepay` and the Safepay transaction ID in
   the notes.
3. Match the settlement to your bank statement. Note the PKR amount and FX rate if the invoice
   is in USD.
4. Keep the Safepay settlement report with your tax records.

### 5.4 Important differences from Paddle

- Safepay is **not** a merchant of record. You are the seller; sales tax, refunds, and
  chargebacks are your responsibility.
- Pricing/fees can change by volume and negotiation; confirm before quoting.
- Do not assume a payment link supports recurring billing until you have tested it on your
  account.

---

## 6. Console invoice workflow (the happy path)

1. **Plan:** Billing → Plans → confirm the published plan (e.g. **Chatbot Starter** at
   $149/month + $500 setup).
2. **Client + subscription:** create the client (or open an existing one) → Billing tab →
   create subscription (status `active` or `trial`, start date, period end).
3. **Invoice:** Billing → New invoice → choose client/subscription. Check **Add one-time setup
   fee on the first invoice** only if it has not been billed before. Set the due date.
4. **Link:** create the hosted link at the provider, then attach it to the invoice. The invoice
   shows **Copy link**.
5. **Send:** copy the invoice details/link and send them to the client. There is no built-in
   email sender in v1.
6. **Paid:** after the provider/bank confirms, click **Record payment**, choose the method and
   date, and save. The invoice becomes `paid`; the subscription period extends.
7. **Export:** Billing → Export CSV for accounting.

Invoice numbers use `INV-YYYY-NNNN` and are unique. Do not reuse or renumber paid invoices; if
you make a mistake, void it and issue a new one.

---

## 7. Reconciliation, refunds, and failures

### Payment received but invoice still open

Check the provider dashboard first. If the payment is captured, record it in the console. If
the provider shows a pending/failed state, do not mark it paid.

### Client paid twice

Refund the duplicate in the provider dashboard (Paddle, Polar, Dodo, or Safepay). Mark the
duplicate invoice `void`, and add a note with the refund reference. Do not silently delete the
invoice.

### Refund requested

1. Process the refund in the provider dashboard.
2. In the console, void the invoice (or add a note if it was already paid) and record the
   refund reference.
3. Decide whether to cancel the subscription or shorten the period; update the subscription
   status/period end accordingly.
4. Keep the provider refund receipt.

### Failed recurring payment

1. The provider will retry or mark the subscription past due.
2. Set the console subscription to `past_due`.
3. Send a polite payment link for the outstanding invoice.
4. If it stays unpaid, pause the bot client in the console (`enabled: false` via client
   settings) rather than deleting data.

### Chargeback

With Paddle (MoR), Paddle handles the dispute process; provide order/usage evidence. Same for
Polar and Dodo. With Safepay, respond through the gateway with the same evidence. Pause the
client until resolved.

---

## 8. Tax and Pakistan notes (not legal advice)

- **Paddle as MoR** collects and remits sales tax/VAT/GST on eligible transactions and issues
  receipts. You still have Pakistan income-tax obligations on your net earnings.
- **Polar and Dodo** are also MoRs; confirm which markets and taxes each covers for your store
  before relying on them.
- **Safepay** leaves sales-tax determination and remittance to you. Ask an accountant how
  exported services and local sales tax apply to your business.
- Keep: provider payout statements, invoices, bank credits, FX rates, and proof of service
  delivery. These support export-of-services treatment if it applies.
- Do not advertise a tax treatment you have not confirmed with a Pakistani accountant.
- Business banking: use a dedicated account for the SaaS; do not mix personal funds.
- If you register a company, keep the provider account name consistent with the bank account
  and tax registration to avoid payout holds.

---

## 9. Webhooks (post-v1)

The console is local-only, so it cannot receive provider webhooks directly. When you are ready
to automate, use one of these:

1. **Public receiver on Render** — add a FastAPI route (for example `/billing/webhook`) to the
   public backend, verify the provider signature, and write events to Neon/Postgres. The local
   console reads the events and offers/executes "apply payment".
2. **Serverless receiver** — Cloudflare Worker / Vercel function that verifies the signature and
   writes to the same Postgres database.
3. **Provider → email → manual** — keep v1 manual; zero new infrastructure.

### Paddle events to handle

`transaction.completed`, `transaction.payment_failed`, `subscription.created`,
`subscription.updated`, `subscription.canceled`, `adjustment.created` (refunds/credits).

Verify the `Paddle-Signature` header (HMAC over the raw body with the notification secret).
Use the event ID as an idempotency key and match records by the `custom_data` you set on the
checkout (`client_id`, `invoice_id`) — never trust an amount or client ID supplied only by the
browser.

### Polar and Dodo events

Both offer signed webhook deliveries with their own signature headers and event names. Verify
with the scheme documented in your account, store the raw payload, and reconcile by
transaction/order ID. Test in their sandbox first.

### Safepay events

Use the event/signature scheme documented in your Safepay account. Verify the signature, store
the raw event, and reconcile by transaction ID. Test in sandbox first.

### Rules for any webhook

- Return 2xx quickly; do the database work asynchronously if needed.
- Verify signatures with a constant-time comparison.
- Store the raw payload and a processed flag; ignore duplicates.
- Never log full card data (there should be none) or provider secrets.
- Alert on amount/client mismatches; do not auto-mark paid if the amount differs.
- Keep a manual override in the console for disputes and provider mistakes.

---

## 10. Security rules

- **Never paste provider API secret keys, bank credentials, or card data into the console.**
  The console only needs the hosted checkout URL.
- Use hosted provider pages for all card entry. The console and bot never see card numbers.
- Enable 2FA on Paddle, Polar, Dodo, Safepay, email, and bank accounts.
- Verify the link domain before sending: the exact checkout domain shown in your provider
  dashboard (for Paddle that is normally `paddle.com` / `pay.paddle.io`). The console accepts
  any `http(s)` URL, so operator discipline is the control.
- Do not click payment/provider links in unexpected emails; open the dashboard directly.
- Keep `admin/admin.db`, `admin/.secret.key`, and backups off GitHub and out of shared folders.
- Export CSVs only to a trusted local folder; they contain client contact data, not card data.
- Review billing audit entries after changing plans, invoices, or payment links.

---

## 11. Go-live checklist

- [ ] Paddle seller account approved; payout bank account verified; 2FA on
- [ ] Products created: subscription per tier and one-time setup per tier
- [ ] Sandbox purchase completed, receipt checked, test transaction refunded/cancelled
- [ ] Live checkout link copied (not the sandbox link)
- [ ] Fallback (Polar or Dodo) account created only if Paddle review is blocked
- [ ] Safepay sandbox tested (only if offering Pakistan checkout)
- [ ] Console plan matches provider price and currency
- [ ] Invoice `INV-2026-0001` (or next number) created for the first client
- [ ] Hosted link attached to the invoice; **Copy link** works
- [ ] Refund/terms/privacy pages linked from the site
- [ ] Support email monitored
- [ ] First payment recorded; subscription period extended correctly
- [ ] `admin/admin.db` backed up
- [ ] No card data anywhere in the repo, console, logs, or exports

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| Checkout page says test/sandbox mode | You copied the sandbox link. Copy the live link from the provider dashboard and update the payment link in the console |
| Client paid, console invoice still `sent` | Confirm the payment in the provider dashboard, then **Record payment**. Do not ask the client to pay again |
| Paddle store stuck in review | Answer the product questions (what it does, who buys it, that it is software). If it is declined because the product is a service, keep the chatbot on Paddle and bill services another way |
| Payout not received | Complete KYC/tax forms, confirm the bank account name matches, check the provider's payout minimum/schedule |
| Link rejected by the console | It must be a full `http://` or `https://` URL. `javascript:` and relative URLs are rejected |
| Safepay link won't recur | Use a fresh payment link each month or enable subscriptions on your Safepay account; do not assume a one-time link recurs |
| Currency mismatch | The console ledger is USD by default. Record the actual PKR amount and FX rate in the invoice/payment notes |
| Client disputes the charge | Use the provider dashboard; keep the invoice, chat logs, and delivery evidence. Pause the client if needed |
| Lemon Squeezy store still live | Migrate before 31 Mar 2027. New stores cannot be created since 16 Nov 2026 |

---

## 13. What to do first

1. Create the Paddle seller account and start verification (business review can take time).
2. While waiting, use the console ledger with **manual** payment links for any pilot client.
3. Once approved, create the products, test in sandbox, then send the first live link.
4. If Paddle declines the store, open a Polar or Dodo Payments account and repeat the same
   product/link steps — only the dashboard changes, not the console workflow.
5. Add Safepay only when a client asks for a local Pakistani payment method.
6. Automate webhooks only after the manual flow has run cleanly for a full billing cycle.
