"""Billing ledger: plans, subscriptions, invoices, hosted payment links.

This module never talks to a payment provider. It stores hosted checkout URLs
(Lemon Squeezy / Safepay) and lets the operator reconcile payments manually.
No card numbers are collected or stored anywhere.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from admin import onboarding
from admin.db import now_iso

METHODS = ["bank", "wise", "lemonsqueezy", "safepay", "cash", "other"]
PROVIDERS = ["lemonsqueezy", "safepay", "other"]
BILLING_STATUSES = {"active", "past_due", "suspended"}
BILLING_REMINDERS = {
    "suspended": "Suspend applied locally — push to deploy.",
    "active": "Reactivate applied locally — push to deploy.",
    "past_due": "Billing status updated locally — push to deploy.",
}


def today() -> date:
    return datetime.now(timezone.utc).date()


def money(cents: int | None, currency: str = "USD") -> str:
    cents = int(cents or 0)
    symbol = "$" if (currency or "USD").upper() == "USD" else (currency + " ")
    return f"{symbol}{cents / 100:,.2f}"


def add_months(value: date, months: int) -> date:
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def period_end(start: date, interval: str = "month") -> date:
    if interval == "year":
        return add_months(start, 12)
    return add_months(start, 1)


def effective_status(invoice) -> str:
    status = invoice["status"]
    if status == "sent" and invoice["due_on"] and invoice["due_on"] < today().isoformat():
        return "overdue"
    return status


def next_invoice_number(conn: sqlite3.Connection) -> str:
    year = today().year
    prefix = f"INV-{year}-"
    row = conn.execute(
        "SELECT number FROM invoices WHERE number LIKE ? ORDER BY number DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row:
        try:
            sequence = int(row["number"].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:04d}"


def list_plans(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM plans WHERE active = 1 ORDER BY amount_cents, id"
        ).fetchall()
    ]


def add_plan(
    conn: sqlite3.Connection,
    name: str,
    amount_cents: int,
    setup_fee_cents: int,
    interval: str = "month",
) -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Plan name is required.")
    if amount_cents < 0 or setup_fee_cents < 0:
        raise ValueError("Amounts cannot be negative.")
    if interval not in {"month", "year"}:
        raise ValueError("Interval must be month or year.")
    cur = conn.execute(
        """
        INSERT INTO plans (name, amount_cents, currency, interval, setup_fee_cents, active)
        VALUES (?, ?, 'USD', ?, ?, 1)
        """,
        (name[:80], amount_cents, interval, setup_fee_cents),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_subscriptions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT s.*, COALESCE(c.display_name, s.client_id) AS display_name,
               c.status AS client_status,
               p.name AS plan_name, p.amount_cents, p.currency, p.interval,
               p.setup_fee_cents
        FROM subscriptions s
        LEFT JOIN clients c ON c.id = s.client_id
        JOIN plans p ON p.id = s.plan_id
        ORDER BY s.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def add_subscription(
    conn: sqlite3.Connection,
    client_id: str,
    plan_id: int,
    status: str = "active",
    started_on: str | None = None,
) -> int:
    client = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        raise ValueError("Unknown client.")
    plan = conn.execute(
        "SELECT * FROM plans WHERE id = ? AND active = 1", (plan_id,)
    ).fetchone()
    if not plan:
        raise ValueError("Unknown plan.")
    if status not in {"trial", "active", "past_due", "canceled"}:
        raise ValueError("Invalid subscription status.")
    started = date.fromisoformat(started_on) if started_on else today()
    end = period_end(started, plan["interval"])
    stamp = now_iso()
    cur = conn.execute(
        """
        INSERT INTO subscriptions
            (client_id, plan_id, status, started_on, current_period_end, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (client_id, plan_id, status, started.isoformat(), end.isoformat(), stamp, stamp),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_invoices(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.*, COALESCE(c.display_name, i.client_id) AS display_name,
               (SELECT url FROM payment_links pl
                WHERE pl.invoice_id = i.id AND pl.status = 'active'
                ORDER BY pl.id DESC LIMIT 1) AS payment_url
        FROM invoices i
        LEFT JOIN clients c ON c.id = i.client_id
        ORDER BY i.issued_on DESC, i.id DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["effective_status"] = effective_status(item)
        result.append(item)
    return result


def add_invoice(
    conn: sqlite3.Connection,
    client_id: str,
    amount_cents: int,
    subscription_id: int | None = None,
    due_on: str | None = None,
    notes: str | None = None,
    status: str = "draft",
) -> dict:
    if conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone() is None:
        raise ValueError("Unknown client.")
    if amount_cents <= 0:
        raise ValueError("Invoice amount must be greater than zero.")
    if status not in {"draft", "sent"}:
        raise ValueError("Invalid invoice status.")
    number = next_invoice_number(conn)
    stamp = now_iso()
    cur = conn.execute(
        """
        INSERT INTO invoices
            (client_id, subscription_id, number, issued_on, due_on, amount_cents,
             currency, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'USD', ?, ?, ?)
        """,
        (
            client_id,
            subscription_id,
            number,
            today().isoformat(),
            due_on or today().isoformat(),
            amount_cents,
            status,
            (notes or "").strip()[:1000] or None,
            stamp,
        ),
    )
    conn.commit()
    return {"id": int(cur.lastrowid), "number": number}


def invoice(conn: sqlite3.Connection, invoice_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT i.*, COALESCE(c.display_name, i.client_id) AS display_name,
               p.interval
        FROM invoices i
        LEFT JOIN clients c ON c.id = i.client_id
        LEFT JOIN subscriptions s ON s.id = i.subscription_id
        LEFT JOIN plans p ON p.id = s.plan_id
        WHERE i.id = ?
        """,
        (invoice_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["effective_status"] = effective_status(item)
    return item


def mark_sent(conn: sqlite3.Connection, invoice_id: int) -> None:
    conn.execute(
        "UPDATE invoices SET status = 'sent' WHERE id = ? AND status IN ('draft', 'sent')",
        (invoice_id,),
    )
    conn.commit()


def void_invoice(conn: sqlite3.Connection, invoice_id: int) -> None:
    conn.execute(
        "UPDATE invoices SET status = 'void' WHERE id = ? AND status != 'paid'",
        (invoice_id,),
    )
    conn.commit()


def record_payment(
    conn: sqlite3.Connection, invoice_id: int, method: str, paid_on: str | None = None
) -> dict:
    item = invoice(conn, invoice_id)
    if not item:
        raise ValueError("Invoice not found.")
    if item["status"] == "paid":
        raise ValueError("That invoice is already paid.")
    if method not in METHODS:
        raise ValueError("Invalid payment method.")
    paid = date.fromisoformat(paid_on) if paid_on else today()
    conn.execute(
        """
        UPDATE invoices
        SET status = 'paid', paid_on = ?, method = ?
        WHERE id = ?
        """,
        (paid.isoformat(), method, invoice_id),
    )

    extended = None
    if item["subscription_id"]:
        sub = conn.execute(
            "SELECT s.*, p.interval FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.id = ?",
            (item["subscription_id"],),
        ).fetchone()
        if sub:
            current = (
                date.fromisoformat(sub["current_period_end"])
                if sub["current_period_end"]
                else paid
            )
            base = max(current, paid)
            extended = period_end(base, sub["interval"])
            conn.execute(
                """
                UPDATE subscriptions
                SET status = 'active', current_period_end = ?, updated_at = ?
                WHERE id = ?
                """,
                (extended.isoformat(), now_iso(), sub["id"]),
            )
    conn.commit()
    return {"extended_to": extended.isoformat() if extended else None}


def summary(conn: sqlite3.Connection) -> dict:
    month_start = today().replace(day=1).isoformat()
    mrr = conn.execute(
        """
        SELECT COALESCE(SUM(p.amount_cents), 0) AS total
        FROM subscriptions s JOIN plans p ON p.id = s.plan_id
        WHERE s.status = 'active' AND p.interval = 'month'
        """
    ).fetchone()["total"]
    active_subs = conn.execute(
        "SELECT COUNT(*) AS n FROM subscriptions WHERE status = 'active'"
    ).fetchone()["n"]
    collected = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM invoices WHERE status = 'paid' AND paid_on >= ?",
        (month_start,),
    ).fetchone()["total"]
    paid_count = conn.execute(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'paid' AND paid_on >= ?",
        (month_start,),
    ).fetchone()["n"]
    outstanding_row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total, COUNT(*) AS n FROM invoices WHERE status = 'sent'"
    ).fetchone()
    overdue_row = conn.execute(
        "SELECT COUNT(*) AS n FROM invoices WHERE status = 'sent' AND due_on < ?",
        (today().isoformat(),),
    ).fetchone()
    return {
        "mrr_cents": mrr,
        "active_subs": active_subs,
        "collected_cents": collected,
        "paid_count": paid_count,
        "outstanding_cents": outstanding_row["total"],
        "outstanding_count": outstanding_row["n"],
        "overdue_count": overdue_row["n"],
    }


def list_payment_links(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pl.*, COALESCE(c.display_name, pl.client_id) AS display_name,
               i.number AS invoice_number
        FROM payment_links pl
        LEFT JOIN clients c ON c.id = pl.client_id
        LEFT JOIN invoices i ON i.id = pl.invoice_id
        ORDER BY pl.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _safe_url(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise ValueError("That URL is too long.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a full https:// checkout URL.")
    return url


def add_payment_link(
    conn: sqlite3.Connection,
    client_id: str,
    label: str,
    url: str,
    provider: str,
    kind: str = "checkout",
    invoice_id: int | None = None,
    amount_cents: int | None = None,
) -> int:
    if conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone() is None:
        raise ValueError("Unknown client.")
    if provider not in PROVIDERS:
        raise ValueError("Unknown payment provider.")
    if kind not in {"checkout", "portal"}:
        raise ValueError("Unknown link type.")
    url = _safe_url(url)
    label = (label or "").strip()[:80] or "Payment link"
    if invoice_id:
        owned = conn.execute(
            "SELECT id FROM invoices WHERE id = ? AND client_id = ?",
            (invoice_id, client_id),
        ).fetchone()
        if not owned:
            raise ValueError("That invoice does not belong to this client.")
    cur = conn.execute(
        """
        INSERT INTO payment_links
            (client_id, invoice_id, provider, kind, label, url, amount_cents,
             currency, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'USD', 'active', ?)
        """,
        (
            client_id,
            invoice_id,
            provider,
            kind,
            label,
            url,
            amount_cents,
            now_iso(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def archive_payment_link(conn: sqlite3.Connection, link_id: int) -> None:
    conn.execute(
        "UPDATE payment_links SET status = 'archived' WHERE id = ?", (link_id,)
    )
    conn.commit()


def client_billing(conn: sqlite3.Connection, client_id: str) -> dict:
    sub = conn.execute(
        """
        SELECT s.*, p.name AS plan_name, p.amount_cents, p.currency, p.interval,
               p.setup_fee_cents
        FROM subscriptions s JOIN plans p ON p.id = s.plan_id
        WHERE s.client_id = ? ORDER BY s.id DESC LIMIT 1
        """,
        (client_id,),
    ).fetchone()
    invoices = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM invoices WHERE client_id = ? ORDER BY issued_on DESC, id DESC",
            (client_id,),
        ).fetchall()
    ]
    for item in invoices:
        item["effective_status"] = effective_status(item)
    links = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM payment_links WHERE client_id = ? ORDER BY id DESC",
            (client_id,),
        ).fetchall()
    ]
    return {
        "subscription": dict(sub) if sub else None,
        "invoices": invoices,
        "links": links,
    }


def set_billing_status(
    conn: sqlite3.Connection, client_id: str, status: str
) -> str:
    """Write the client's billing status to its YAML and return the deploy reminder."""
    status = (status or "").strip().lower()
    if status not in BILLING_STATUSES:
        raise ValueError("Invalid billing status.")
    if (
        conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
        is None
    ):
        raise ValueError("Unknown client.")

    config = onboarding.read_config(client_id)
    config["billing_status"] = status
    onboarding._write_yaml(client_id, dict(config))
    return BILLING_REMINDERS[status]
