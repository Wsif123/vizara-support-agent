"""Vizara operator console — local-only FastAPI application."""

from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from admin import auth, billing, db, keys, metrics, onboarding
from admin.security import SecurityMiddleware
from src import key_store
from src.registry import invalidate_cache as invalidate_client_cache

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    startup()
    yield


app = FastAPI(
    title="Vizara Operator Console",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup() -> None:
    db.init_db()
    with db.db() as conn:
        try:
            onboarding.sync_admin_clients(conn)
        except Exception as exc:
            print(f"[admin] client sync failed: {type(exc).__name__}")
        try:
            imported = keys.import_env_keys(conn)
            if imported:
                print(f"[admin] imported {imported} Groq key(s) from .env into encrypted storage")
        except Exception as exc:
            print(f"[admin] environment key import skipped: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _as_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


def _money(cents, currency="USD") -> str:
    return billing.money(cents, currency)


def _short_dt(value) -> str:
    parsed = _as_dt(value)
    return parsed.astimezone().strftime("%b %d, %Y · %H:%M") if parsed else "—"


def _date(value) -> str:
    parsed = _as_dt(value)
    if parsed:
        return parsed.astimezone().strftime("%b %d, %Y")
    return str(value) if value else "—"


def _timeago(value) -> str:
    parsed = _as_dt(value)
    if not parsed:
        return "—"
    delta = datetime.now(timezone.utc) - parsed
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 604800:
        return f"{seconds // 86400}d ago"
    return parsed.astimezone().strftime("%b %d")


def _initials(value) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(value or "?"))
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


STATUS_META = {
    "active": ("green", "Active"),
    "paused": ("gray", "Paused"),
    "archived": ("gray", "Archived"),
    "trial": ("blue", "Trial"),
    "past_due": ("red", "Past due"),
    "canceled": ("gray", "Canceled"),
    "disabled": ("gray", "Disabled"),
    "invalid": ("red", "Invalid"),
    "cooldown": ("amber", "Cooling"),
    "draft": ("gray", "Draft"),
    "sent": ("blue", "Sent"),
    "paid": ("green", "Paid"),
    "overdue": ("red", "Overdue"),
    "void": ("gray", "Void"),
    "success": ("green", "OK"),
    "429": ("amber", "429"),
    "error": ("red", "Error"),
    "resolved": ("green", "Resolved"),
    "open": ("amber", "Open"),
}


def _status_class(value) -> str:
    return STATUS_META.get(str(value), ("gray", str(value)))[0]


def _status_label(value) -> str:
    return STATUS_META.get(str(value), ("gray", str(value)))[1]


templates.env.filters.update(
    {
        "money": _money,
        "dt": _short_dt,
        "date": _date,
        "timeago": _timeago,
        "initials": _initials,
        "status_class": _status_class,
        "status_label": _status_label,
    }
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


def _base(request: Request, conn, active: str, title: str, subtitle: str = "") -> dict:
    session = auth.get_session(request, conn)
    if not session:
        raise auth.AuthRequired()
    flash_message, flash_kind = auth.take_flash(conn, session["token_hash"])
    pool = keys.summary(conn)
    return {
        "request": request,
        "session": session,
        "csrf_token": session["csrf_token"],
        "active": active,
        "page_title": title,
        "page_subtitle": subtitle,
        "flash_message": flash_message,
        "flash_kind": flash_kind,
        "pool": pool,
    }


def _redirect(
    url: str,
    conn=None,
    session: Optional[dict] = None,
    message: str | None = None,
    kind: str = "ok",
) -> RedirectResponse:
    if message and conn is not None and session is not None:
        auth.set_flash(conn, session["token_hash"], message, kind)
    return RedirectResponse(url, status_code=303)


def _check_csrf(request: Request, session: dict, token: str | None) -> None:
    if not auth.csrf_ok(session, token):
        raise HTTPException(status_code=403, detail="Invalid security token. Reload and try again.")


def _audit(conn, request: Request, action: str, target: str | None = None, details: str | None = None):
    db.audit(
        conn,
        action,
        target=target,
        details=(details or "")[:1000] or None,
        ip=_client_ip(request),
    )


def _cents(text: str | None, field: str = "Amount") -> int:
    value = (text or "").strip().replace(",", "").replace("$", "")
    if not value:
        return 0
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field} must be a number.")
    if amount < 0:
        raise ValueError(f"{field} cannot be negative.")
    return int((amount * 100).quantize(Decimal("1")))


def _page_int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _chart(daily: list[dict]) -> dict:
    values = [item["count"] for item in daily] or [0]
    maximum = max(values) or 1
    width, height, base = 720.0, 190.0, 166.0
    count = len(values)
    step = width / (count - 1) if count > 1 else width
    coords = []
    for index, value in enumerate(values):
        x = index * step
        y = base - (value / maximum) * 130
        coords.append((round(x, 1), round(y, 1)))
    points = " ".join(f"{x},{y}" for x, y in coords)
    area = f"M0,{base} L" + " L".join(f"{x},{y}" for x, y in coords) + f" L{width},{base} Z"
    label_indexes = sorted({0, count // 2, count - 1})
    labels = []
    for index in label_indexes:
        stamp = daily[index]["date"] if index < len(daily) else ""
        try:
            label = datetime.fromisoformat(stamp).strftime("%b %d")
        except ValueError:
            label = stamp
        labels.append({"x": round(index * step, 1), "label": label})
    return {
        "points": points,
        "area": area,
        "labels": labels,
        "max": maximum,
        "width": int(width),
        "height": int(height),
        "base": int(base),
    }


_render_health_cache: dict = {"at": 0.0, "data": None}


def _render_health() -> dict | None:
    url = (os.getenv("RENDER_DEMO_URL") or "").strip().rstrip("/")
    if not url:
        return None
    now = time.time()
    if now - _render_health_cache["at"] < 60 and _render_health_cache["data"]:
        return _render_health_cache["data"]
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(url + "/health")
        data = {
            "ok": resp.status_code == 200,
            "status": resp.status_code,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "url": url,
        }
    except httpx.HTTPError as exc:
        data = {
            "ok": False,
            "status": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "url": url,
            "error": type(exc).__name__,
        }
    _render_health_cache.update({"at": now, "data": data})
    return data


def _client_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.*,
            (SELECT p.name FROM subscriptions s JOIN plans p ON p.id = s.plan_id
             WHERE s.client_id = c.id ORDER BY s.id DESC LIMIT 1) AS plan_name,
            (SELECT s.status FROM subscriptions s
             WHERE s.client_id = c.id ORDER BY s.id DESC LIMIT 1) AS sub_status
        FROM clients c
        ORDER BY c.created_at DESC, c.id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _client_names(conn) -> dict[str, str]:
    return {
        row["id"]: row["display_name"]
        for row in conn.execute("SELECT id, display_name FROM clients").fetchall()
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _auth_page(
    request: Request,
    mode: str,
    error: str | None = None,
    status_code: int = 200,
    next_url: str = "/",
):
    token = auth.preauth_token(request)
    response = _render(
        request,
        "auth.html",
        {
            "request": request,
            "mode": mode,
            "csrf_token": token,
            "next": next_url,
            "error": error,
        },
        status_code=status_code,
    )
    auth.set_preauth_cookie(response, token)
    return response


@app.exception_handler(auth.AuthRequired)
async def _auth_required_handler(request: Request, exc: auth.AuthRequired):
    target = request.url.path + (("?" + request.url.query) if request.url.query else "")
    if not target.startswith("/") or target.startswith("//"):
        target = "/"
    from urllib.parse import quote

    return RedirectResponse("/login?next=" + quote(target, safe=""), status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with db.db() as conn:
        if not conn.execute("SELECT id FROM admin_users LIMIT 1").fetchone():
            return RedirectResponse("/setup", status_code=303)
        session = auth.get_session(request, conn)
        if session:
            return RedirectResponse("/", status_code=303)
        return _auth_page(
            request, "login", next_url=request.query_params.get("next", "/")
        )


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    password: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form("/"),
):
    with db.db() as conn:
        user = conn.execute("SELECT * FROM admin_users WHERE id = 1").fetchone()
        if not user:
            return RedirectResponse("/setup", status_code=303)
        if not auth.preauth_csrf_ok(request, csrf_token):
            return _auth_page(
                request,
                "login",
                "Your security token expired. Try again.",
                400,
                next,
            )
        if not auth.login_allowed(request):
            return _auth_page(
                request,
                "login",
                "Too many attempts. Wait a minute, then try again.",
                429,
                next,
            )
        if not auth.verify_password(password, user["password_hash"]):
            auth.login_failed(request)
            db.audit(
                conn,
                "login_failed",
                details="Incorrect password",
                ip=_client_ip(request),
            )
            conn.commit()
            return _auth_page(
                request, "login", "That password is not correct.", 401, next
            )

        auth.login_succeeded(request)
        token, _ = auth.create_session(conn, request)
        db.audit(conn, "login", details="Successful sign-in", ip=_client_ip(request))
        conn.commit()
        response = RedirectResponse(
            next
            if next.startswith("/")
            and not next.startswith("//")
            and "\\" not in next
            and "\r" not in next
            and "\n" not in next
            else "/",
            status_code=303,
        )
        auth.set_session_cookie(response, token)
        return response


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    with db.db() as conn:
        if conn.execute("SELECT id FROM admin_users LIMIT 1").fetchone():
            return RedirectResponse("/login", status_code=303)
        return _auth_page(request, "setup")


@app.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        if conn.execute("SELECT id FROM admin_users LIMIT 1").fetchone():
            return RedirectResponse("/login", status_code=303)
        if not auth.preauth_csrf_ok(request, csrf_token):
            return _auth_page(
                request,
                "setup",
                "Your security token expired. Reload the page.",
                400,
            )
        if not auth.login_allowed(request):
            return _auth_page(
                request, "setup", "Too many attempts. Wait a minute.", 429
            )
        problem = auth.password_problem(password)
        if problem:
            auth.login_failed(request)
            return _auth_page(request, "setup", problem, 400)
        if password != confirm:
            auth.login_failed(request)
            return _auth_page(
                request, "setup", "The two passwords do not match.", 400
            )
        stamp = db.now_iso()
        conn.execute(
            "INSERT INTO admin_users (id, password_hash, created_at, updated_at) VALUES (1, ?, ?, ?)",
            (auth.hash_password(password), stamp, stamp),
        )
        token, _ = auth.create_session(conn, request)
        db.audit(conn, "admin_created", details="First-run password set", ip=_client_ip(request))
        conn.commit()
        response = RedirectResponse("/", status_code=303)
        auth.set_session_cookie(response, token)
        return response


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if session and auth.csrf_ok(session, csrf_token):
            auth.delete_session(conn, session["token_hash"])
            db.audit(conn, "logout", ip=_client_ip(request))
            conn.commit()
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def overview(request: Request, days: str = "7"):
    days = _page_int(days, 7, 1, 90)
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "overview",
            "Overview",
            "Everything happening across your support bots, in one place.",
        )
        stats = metrics.stats(days)
        usage = metrics.client_usage(days)
        clients = _client_rows(conn)
        for client in clients:
            client["usage"] = usage.get(client["id"], {})
        leaderboard = sorted(
            clients,
            key=lambda item: item["usage"].get("messages", 0),
            reverse=True,
        )
        names = {client["id"]: client["display_name"] for client in clients}
        escalations = metrics.recent_escalations(5)
        resolved_ids = set()
        if escalations:
            placeholders = ",".join("?" for _ in escalations)
            resolved_ids = {
                row["conversation_id"]
                for row in conn.execute(
                    f"SELECT conversation_id FROM conversation_notes WHERE conversation_id IN ({placeholders})",
                    tuple(item["id"] for item in escalations),
                ).fetchall()
            }
        for item in escalations:
            item["client_name"] = names.get(item["client_id"], item["client_id"] or "Unknown")
            item["resolved"] = item["id"] in resolved_ids

        configs = {item["id"]: item for item in onboarding.list_configs()}
        index_rows = []
        for client in clients:
            if client["id"] not in configs:
                continue
            status = onboarding.index_status(client["id"], deep=False)
            status["client_name"] = client["display_name"]
            status["client_id"] = client["id"]
            index_rows.append(status)

        model = key_store.get_groq_model()
        billing_summary = billing.summary(conn)

        ctx.update(
            {
                "stats": stats,
                "leaderboard": leaderboard,
                "clients": clients,
                "active_clients": sum(1 for c in clients if c["status"] == "active"),
                "escalations": escalations,
                "open_escalations": metrics.open_escalation_count(conn),
                "mrr": billing_summary["mrr_cents"],
                "chart": _chart(stats["daily"]),
                "days": days,
                "key_pool": ctx["pool"],
                "index_rows": index_rows,
                "source": metrics.source_status(),
                "files": metrics.system_files(),
                "render": _render_health(),
                "model": model,
                "has_activity": stats["messages"] > 0,
            }
        )
        return _render(request, "overview.html", ctx)


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request):
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "clients",
            "Clients",
            "Accounts, plans, usage, and billing status.",
        )
        usage = metrics.client_usage(7)
        clients = _client_rows(conn)
        for client in clients:
            client["usage"] = usage.get(client["id"], {})
        ctx.update(
            {
                "clients": clients,
                "total_clients": len(clients),
                "active_clients": sum(1 for c in clients if c["status"] == "active"),
            }
        )
        return _render(request, "clients.html", ctx)


@app.get("/clients/new", response_class=HTMLResponse)
def new_client_page(request: Request):
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "clients",
            "New client",
            "Create the config, save content, and build the index in one step.",
        )
        ctx.update({"plans": billing.list_plans(conn)})
        return _render(request, "client_new.html", ctx)


@app.post("/clients/new")
def new_client_submit(
    request: Request,
    client_id: str = Form(...),
    display_name: str = Form(...),
    domain: str = Form(""),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    persona: str = Form(""),
    top_k: str = Form("6"),
    score_threshold: str = Form("0.55"),
    plan_id: str = Form(""),
    knowledge: str = Form(""),
    csrf_token: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            result = onboarding.create_client(
                conn,
                client_id=client_id,
                display_name=display_name,
                domain=domain,
                contact_name=contact_name,
                contact_email=contact_email,
                persona=persona,
                top_k=int(top_k or 6),
                score_threshold=float(score_threshold or 0.55),
                uploads=files or [],
                pasted_content=knowledge,
            )
            if plan_id:
                try:
                    billing.add_subscription(
                        conn, result["client_id"], int(plan_id), status="active"
                    )
                except ValueError as exc:
                    result["billing_error"] = str(exc)
            _audit(
                conn,
                request,
                "client_created",
                target=result["client_id"],
                details=f"{result['chunks']} chunks from {len(result['files'])} file(s)",
            )
            conn.commit()
            invalidate_client_cache()
            message = (
                f"Created {result['display_name']} and indexed {result['chunks']} chunks."
            )
            kind = "ok"
            if result.get("ingest_error"):
                message += " The index was not built — open Content and run Reindex."
                kind = "error"
            if result.get("billing_error"):
                message += f" Billing: {result['billing_error']}"
                kind = "error"
            return _redirect(
                f"/clients/{result['client_id']}", conn, session, message, kind
            )
        except ValueError as exc:
            return _redirect("/clients/new", conn, session, str(exc), "error")
        except Exception as exc:
            print(f"[admin] onboarding failed: {type(exc).__name__}")
            return _redirect(
                "/clients/new",
                conn,
                session,
                "Could not create the client. Check the server console for the error type.",
                "error",
            )


@app.get("/clients/{client_id}", response_class=HTMLResponse)
def client_detail(request: Request, client_id: str):
    tab = request.query_params.get("tab", "overview")
    if tab not in {"overview", "conversations", "billing", "content", "settings"}:
        tab = "overview"
    with db.db() as conn:
        ctx = _base(request, conn, "clients", client_id, "")
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Client not found.")
        client = dict(row)
        try:
            config = onboarding.read_config(client_id)
        except Exception:
            config = {}
        ctx.update(
            {
                "client": client,
                "config": config,
                "tab": tab,
                "bot_url": (
                    os.getenv("PUBLIC_BOT_URL") or "http://localhost:8000"
                ).strip().rstrip("/"),
                "page_title": client["display_name"],
                "page_subtitle": config.get("domain") or "No domain registered",
                "usage": metrics.client_usage(30).get(client_id, {}),
                "billing": billing.client_billing(conn, client_id),
                "plans": billing.list_plans(conn),
                "content": onboarding.content_files(config) if config else [],
                "index": onboarding.index_status(client_id, deep=tab == "content"),
                "audit": [
                    dict(item)
                    for item in conn.execute(
                        "SELECT * FROM audit_log WHERE target = ? ORDER BY id DESC LIMIT 8",
                        (client_id,),
                    ).fetchall()
                ],
            }
        )
        if tab == "conversations":
            scope = request.query_params.get("scope", "all")
            rows, total = metrics.conversation_rows(
                days=90,
                client_id=client_id,
                escalated=True if scope == "escalated" else None,
                limit=50,
                offset=0,
            )
            selected_id = request.query_params.get("c")
            selected = None
            if selected_id:
                candidate = metrics.conversation(_page_int(selected_id, 0, 1, 10**9))
                if candidate and candidate.get("client_id") == client_id:
                    selected = candidate
            if not selected and rows:
                selected = metrics.conversation(rows[0]["id"])
            resolved = set()
            if rows:
                placeholders = ",".join("?" for _ in rows)
                resolved = {
                    item["conversation_id"]
                    for item in conn.execute(
                        f"SELECT conversation_id FROM conversation_notes WHERE conversation_id IN ({placeholders})",
                        tuple(item["id"] for item in rows),
                    ).fetchall()
                }
            ctx.update(
                {
                    "conversations": rows,
                    "conversation_total": total,
                    "scope": scope,
                    "selected": selected,
                    "resolved_ids": resolved,
                    "selected_note": conn.execute(
                        "SELECT * FROM conversation_notes WHERE conversation_id = ?",
                        (selected["id"],),
                    ).fetchone()
                    if selected
                    else None,
                }
            )
        return _render(request, "client_detail.html", ctx)


@app.post("/clients/{client_id}/status")
def client_status(
    request: Request,
    client_id: str,
    status: str = Form(...),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        if status not in {"active", "paused"}:
            return _redirect(f"/clients/{client_id}", conn, session, "Invalid status.", "error")
        try:
            onboarding.update_client(client_id, enabled=status == "active")
        except Exception:
            return _redirect(
                f"/clients/{client_id}",
                conn,
                session,
                "Config could not be updated.",
                "error",
            )
        conn.execute(
            "UPDATE clients SET status = ?, updated_at = ? WHERE id = ?",
            (status, db.now_iso(), client_id),
        )
        _audit(conn, request, "client_status", client_id, status)
        conn.commit()
        invalidate_client_cache()
        return _redirect(
            f"/clients/{client_id}",
            conn,
            session,
            f"{client_id} is now {status}. The public bot picks this up within 2 seconds.",
        )


@app.post("/clients/{client_id}/settings")
def client_settings(
    request: Request,
    client_id: str,
    display_name: str = Form(...),
    domain: str = Form(""),
    contact_name: str = Form(""),
    contact_email: str = Form(""),
    persona: str = Form(""),
    top_k: str = Form("6"),
    score_threshold: str = Form("0.55"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            onboarding.update_client(
                client_id,
                display_name=display_name,
                domain=domain,
                contact_name=contact_name,
                contact_email=contact_email,
                persona=persona,
                top_k=int(top_k or 6),
                score_threshold=float(score_threshold or 0.55),
            )
            conn.execute(
                """
                UPDATE clients SET display_name = ?, domain = ?, contact_name = ?,
                    contact_email = ?, updated_at = ? WHERE id = ?
                """,
                (
                    display_name.strip(),
                    onboarding.normalize_domain(domain),
                    contact_name.strip() or None,
                    contact_email.strip() or None,
                    db.now_iso(),
                    client_id,
                ),
            )
            _audit(conn, request, "client_settings", client_id, "Client and retrieval settings updated")
            conn.commit()
            invalidate_client_cache()
            return _redirect(
                f"/clients/{client_id}?tab=settings",
                conn,
                session,
                "Settings saved. Registry reloaded.",
            )
        except ValueError as exc:
            return _redirect(
                f"/clients/{client_id}?tab=settings", conn, session, str(exc), "error"
            )


@app.post("/clients/{client_id}/reindex")
def client_reindex(request: Request, client_id: str, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            chunks = onboarding.reindex(client_id)
            _audit(conn, request, "client_reindex", client_id, f"{chunks} chunks")
            conn.commit()
            return _redirect(
                f"/clients/{client_id}?tab=content",
                conn,
                session,
                f"Index rebuilt: {chunks} chunks.",
            )
        except Exception as exc:
            print(f"[admin] reindex failed for {client_id}: {type(exc).__name__}")
            return _redirect(
                f"/clients/{client_id}?tab=content",
                conn,
                session,
                "Reindex failed. Check the content files and the server console.",
                "error",
            )


@app.post("/clients/{client_id}/content")
def client_content(
    request: Request,
    client_id: str,
    csrf_token: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            sources = onboarding.replace_content(client_id, files or [])
            chunks = onboarding.reindex(client_id)
            _audit(
                conn,
                request,
                "client_content",
                client_id,
                f"{len(sources)} source(s); {chunks} chunks",
            )
            conn.commit()
            return _redirect(
                f"/clients/{client_id}?tab=content",
                conn,
                session,
                f"Content updated and indexed ({chunks} chunks).",
            )
        except ValueError as exc:
            return _redirect(
                f"/clients/{client_id}?tab=content", conn, session, str(exc), "error"
            )
        except Exception as exc:
            print(f"[admin] content update failed for {client_id}: {type(exc).__name__}")
            return _redirect(
                f"/clients/{client_id}?tab=content",
                conn,
                session,
                "Upload saved but reindexing failed. Try Reindex again.",
                "error",
            )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@app.get("/conversations", response_class=HTMLResponse)
def conversations_page(request: Request):
    days = _page_int(request.query_params.get("days"), 30, 1, 365)
    scope = request.query_params.get("scope", "all")
    client_id = request.query_params.get("client", "")
    search = (request.query_params.get("q") or "").strip()[:200]
    page = _page_int(request.query_params.get("page"), 0, 0, 100000)
    limit = 50
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "conversations",
            "Conversations",
            "Search transcripts, inspect escalations, and mark follow-ups resolved.",
        )
        rows, total = metrics.conversation_rows(
            days=days,
            client_id=client_id or None,
            escalated=True if scope == "escalated" else None,
            search=search or None,
            limit=limit,
            offset=page * limit,
        )
        names = _client_names(conn)
        for row in rows:
            row["client_name"] = names.get(row["client_id"], row["client_id"] or "Unknown")
        resolved = set()
        if rows:
            placeholders = ",".join("?" for _ in rows)
            resolved = {
                item["conversation_id"]
                for item in conn.execute(
                    f"SELECT conversation_id FROM conversation_notes WHERE conversation_id IN ({placeholders})",
                    tuple(item["id"] for item in rows),
                ).fetchall()
            }
        selected = None
        selected_id = request.query_params.get("c")
        if selected_id:
            candidate = metrics.conversation(_page_int(selected_id, 0, 1, 10**9))
            if candidate:
                selected = candidate
        if not selected and rows:
            selected = metrics.conversation(rows[0]["id"])
        if selected:
            selected["client_name"] = names.get(
                selected["client_id"], selected["client_id"] or "Unknown"
            )
        note = None
        if selected:
            note = conn.execute(
                "SELECT * FROM conversation_notes WHERE conversation_id = ?",
                (selected["id"],),
            ).fetchone()
        ctx.update(
            {
                "conversations": rows,
                "conversation_total": total,
                "clients": _client_rows(conn),
                "client_filter": client_id,
                "scope": scope,
                "days": days,
                "search": search,
                "page": page,
                "has_next": (page + 1) * limit < total,
                "resolved_ids": resolved,
                "selected": selected,
                "selected_note": note,
                "open_escalations": metrics.open_escalation_count(conn),
                "source": metrics.source_status(),
            }
        )
        return _render(request, "conversations.html", ctx)


@app.post("/conversations/{conversation_id}/resolve")
def resolve_conversation(
    request: Request,
    conversation_id: int,
    note: str = Form(""),
    next: str = Form("/conversations"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        conversation = metrics.conversation(conversation_id)
        if not conversation:
            return _redirect("/conversations", conn, session, "Conversation not found.", "error")
        conn.execute(
            """
            INSERT INTO conversation_notes (conversation_id, client_id, resolved_at, resolved_by, note)
            VALUES (?, ?, ?, 'operator', ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                resolved_at = excluded.resolved_at,
                resolved_by = excluded.resolved_by,
                note = excluded.note
            """,
            (
                conversation_id,
                conversation.get("client_id"),
                db.now_iso(),
                note.strip()[:1000] or None,
            ),
        )
        _audit(
            conn,
            request,
            "conversation_resolved",
            target=str(conversation.get("client_id") or ""),
            details=f"Conversation {conversation_id}",
        )
        conn.commit()
        target = next if next.startswith("/conversations") else "/conversations"
        return _redirect(target, conn, session, "Marked resolved.")


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "billing",
            "Billing",
            "Hosted checkout links, manual reconciliation, and a clean invoice ledger.",
        )
        ctx.update(
            {
                "summary": billing.summary(conn),
                "subscriptions": billing.list_subscriptions(conn),
                "invoices": billing.list_invoices(conn),
                "links": billing.list_payment_links(conn),
                "plans": billing.list_plans(conn),
                "clients": _client_rows(conn),
                "methods": billing.METHODS,
                "providers": billing.PROVIDERS,
                "today": billing.today().isoformat(),
                "default_due": (billing.today() + timedelta(days=7)).isoformat(),
                "lemon_url": os.getenv("LEMON_SQUEEZY_STORE_URL", "").strip(),
                "safepay_url": os.getenv("SAFEPAY_STORE_URL", "").strip(),
            }
        )
        return _render(request, "billing.html", ctx)


@app.post("/billing/plans")
def billing_add_plan(
    request: Request,
    name: str = Form(...),
    amount: str = Form("0"),
    setup_fee: str = Form("0"),
    interval: str = Form("month"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            billing.add_plan(
                conn,
                name,
                _cents(amount, "Plan price"),
                _cents(setup_fee, "Setup fee"),
                interval,
            )
            _audit(conn, request, "plan_created", name, "Billing plan added")
            conn.commit()
            return _redirect("/billing", conn, session, "Plan added.")
        except ValueError as exc:
            return _redirect("/billing", conn, session, str(exc), "error")


@app.post("/billing/subscriptions")
def billing_add_subscription(
    request: Request,
    client_id: str = Form(...),
    plan_id: str = Form(...),
    status: str = Form("active"),
    started_on: str = Form(""),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            billing.add_subscription(
                conn, client_id, int(plan_id), status, started_on or None
            )
            _audit(conn, request, "subscription_created", client_id, f"Plan {plan_id}, {status}")
            conn.commit()
            return _redirect("/billing", conn, session, "Subscription created.")
        except ValueError as exc:
            return _redirect("/billing", conn, session, str(exc), "error")


@app.post("/billing/invoices")
def billing_add_invoice(
    request: Request,
    client_id: str = Form(...),
    subscription_id: str = Form(""),
    amount: str = Form(""),
    due_on: str = Form(""),
    notes: str = Form(""),
    status: str = Form("draft"),
    include_setup: str = Form(""),
    payment_url: str = Form(""),
    payment_provider: str = Form("lemonsqueezy"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            sub_id = int(subscription_id) if subscription_id else None
            final_amount = _cents(amount, "Invoice amount") if amount else 0
            if sub_id:
                sub = conn.execute(
                    """
                    SELECT s.id, s.client_id, p.amount_cents, p.setup_fee_cents
                    FROM subscriptions s JOIN plans p ON p.id = s.plan_id
                    WHERE s.id = ?
                    """,
                    (sub_id,),
                ).fetchone()
                if not sub or sub["client_id"] != client_id:
                    raise ValueError("That subscription does not belong to this client.")
                final_amount = sub["amount_cents"]
                already_billed = conn.execute(
                    "SELECT COUNT(*) AS n FROM invoices WHERE subscription_id = ?",
                    (sub_id,),
                ).fetchone()["n"]
                if include_setup and not already_billed:
                    final_amount += sub["setup_fee_cents"]
            if final_amount <= 0:
                raise ValueError("Choose a subscription or enter an amount.")
            result = billing.add_invoice(
                conn,
                client_id,
                final_amount,
                subscription_id=sub_id,
                due_on=due_on or None,
                notes=notes,
                status="sent" if status == "sent" else "draft",
            )
            link_note = ""
            if payment_url.strip():
                try:
                    billing.add_payment_link(
                        conn,
                        client_id,
                        label=f"Checkout for {result['number']}",
                        url=payment_url,
                        provider=payment_provider,
                        invoice_id=result["id"],
                        amount_cents=final_amount,
                    )
                    link_note = " Hosted checkout link attached."
                except ValueError as exc:
                    link_note = f" Invoice saved, but the link was rejected: {exc}"
            _audit(
                conn,
                request,
                "invoice_created",
                client_id,
                f"{result['number']} · {billing.money(final_amount)}",
            )
            conn.commit()
            return _redirect(
                "/billing",
                conn,
                session,
                f"Invoice {result['number']} created.{link_note}",
            )
        except ValueError as exc:
            return _redirect("/billing", conn, session, str(exc), "error")


@app.post("/billing/invoices/{invoice_id}/paid")
def billing_mark_paid(
    request: Request,
    invoice_id: int,
    method: str = Form(...),
    paid_on: str = Form(""),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            result = billing.record_payment(conn, invoice_id, method, paid_on or None)
            _audit(conn, request, "invoice_paid", str(invoice_id), f"Method: {method}")
            conn.commit()
            extra = (
                f" Subscription extended to {result['extended_to']}."
                if result.get("extended_to")
                else ""
            )
            return _redirect("/billing", conn, session, "Payment recorded." + extra)
        except ValueError as exc:
            return _redirect("/billing", conn, session, str(exc), "error")


@app.post("/billing/invoices/{invoice_id}/status")
def billing_invoice_status(
    request: Request,
    invoice_id: int,
    action: str = Form(...),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        if action == "sent":
            billing.mark_sent(conn, invoice_id)
            message = "Invoice marked sent."
        elif action == "void":
            billing.void_invoice(conn, invoice_id)
            message = "Invoice voided."
        else:
            return _redirect("/billing", conn, session, "Unknown action.", "error")
        _audit(conn, request, "invoice_status", str(invoice_id), action)
        conn.commit()
        return _redirect("/billing", conn, session, message)


@app.post("/billing/payment-links")
def billing_add_link(
    request: Request,
    client_id: str = Form(...),
    invoice_id: str = Form(""),
    provider: str = Form("lemonsqueezy"),
    kind: str = Form("checkout"),
    label: str = Form(""),
    url: str = Form(...),
    amount: str = Form(""),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            billing.add_payment_link(
                conn,
                client_id=client_id,
                label=label,
                url=url,
                provider=provider,
                kind=kind,
                invoice_id=int(invoice_id) if invoice_id else None,
                amount_cents=_cents(amount) if amount.strip() else None,
            )
            _audit(conn, request, "payment_link_created", client_id, f"Provider: {provider}")
            conn.commit()
            return _redirect("/billing", conn, session, "Payment link saved.")
        except ValueError as exc:
            return _redirect("/billing", conn, session, str(exc), "error")


@app.post("/billing/payment-links/{link_id}/archive")
def billing_archive_link(
    request: Request, link_id: int, csrf_token: str = Form("")
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        billing.archive_payment_link(conn, link_id)
        _audit(conn, request, "payment_link_archived", str(link_id))
        conn.commit()
        return _redirect("/billing", conn, session, "Payment link archived.")


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

@app.get("/keys", response_class=HTMLResponse)
def keys_page(request: Request):
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "keys",
            "API keys",
            "Encrypted, masked, health-checked, and load-balanced across Groq keys.",
        )
        usage_rows = conn.execute(
            """
            SELECT u.*, k.label FROM key_usage u
            JOIN api_keys k ON k.id = u.key_id
            ORDER BY u.id DESC LIMIT 20
            """
        ).fetchall()
        db_count = conn.execute(
            "SELECT COUNT(*) AS n FROM api_keys WHERE provider = 'groq'"
        ).fetchone()["n"]
        ctx.update(
            {
                "keys": ctx["pool"]["key_list"],
                "usage_rows": [dict(row) for row in usage_rows],
                "db_count": db_count,
                "model": key_store.get_groq_model(),
                "source": "Encrypted local store" if db_count else "Environment variables",
            }
        )
        return _render(request, "keys.html", ctx)


@app.post("/keys")
def key_add(
    request: Request,
    label: str = Form(...),
    secret: str = Form(...),
    weight: str = Form("1"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            result = keys.add_key(conn, label, secret, int(weight or 1))
            _audit(conn, request, "api_key_added", label, "Validated and encrypted")
            conn.commit()
            return _redirect(
                "/keys",
                conn,
                session,
                f"Key added. {result['message']}",
            )
        except ValueError as exc:
            return _redirect("/keys", conn, session, str(exc), "error")
        except Exception as exc:
            print(f"[admin] key add failed: {type(exc).__name__}")
            return _redirect(
                "/keys",
                conn,
                session,
                "Could not encrypt and save the key. Check that cryptography is installed.",
                "error",
            )


@app.post("/keys/{key_id}/test")
def key_test(request: Request, key_id: int, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            result = keys.test_key(conn, key_id)
            _audit(conn, request, "api_key_tested", str(key_id), result["message"])
            conn.commit()
            return _redirect(
                "/keys",
                conn,
                session,
                result["message"],
                "ok" if result["ok"] else "error",
            )
        except ValueError as exc:
            return _redirect("/keys", conn, session, str(exc), "error")


@app.post("/keys/{key_id}/toggle")
def key_toggle(request: Request, key_id: int, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        try:
            new_status = keys.toggle_key(conn, key_id)
            _audit(conn, request, "api_key_toggled", str(key_id), new_status)
            conn.commit()
            return _redirect("/keys", conn, session, f"Key {new_status}.")
        except ValueError as exc:
            return _redirect("/keys", conn, session, str(exc), "error")


@app.post("/keys/{key_id}/weight")
def key_weight(
    request: Request,
    key_id: int,
    weight: str = Form("1"),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        keys.update_weight(conn, key_id, int(weight or 1))
        _audit(conn, request, "api_key_weight", str(key_id), f"Weight {weight}")
        conn.commit()
        return _redirect("/keys", conn, session, "Key weight updated.")


@app.post("/keys/{key_id}/delete")
def key_delete(
    request: Request,
    key_id: int,
    confirm: str = Form(""),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        if confirm.strip().upper() != "DELETE":
            return _redirect(
                "/keys", conn, session, "Type DELETE to confirm key removal.", "error"
            )
        keys.delete_key(conn, key_id)
        _audit(conn, request, "api_key_deleted", str(key_id))
        conn.commit()
        return _redirect("/keys", conn, session, "Key deleted.")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    with db.db() as conn:
        ctx = _base(
            request,
            conn,
            "settings",
            "Settings",
            "Local console configuration. Secrets never appear on this page.",
        )
        admin = conn.execute("SELECT * FROM admin_users WHERE id = 1").fetchone()
        backups = []
        for path in sorted(db.BACKUP_DIR.glob("*.db"), reverse=True)[:8]:
            backups.append(
                {
                    "name": path.name,
                    "size_kb": round(path.stat().st_size / 1024, 1),
                    "modified": _short_dt(
                        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    ),
                }
            )
        session_count = conn.execute(
            "SELECT COUNT(*) AS n FROM admin_sessions"
        ).fetchone()["n"]
        ctx.update(
            {
                "admin": dict(admin) if admin else None,
                "source": metrics.source_status(),
                "files": metrics.system_files(),
                "model": key_store.get_groq_model(),
                "formspree": bool(os.getenv("NOTIFY_FORMSPREE_URL", "").strip()),
                "notify_email": os.getenv("NOTIFY_EMAIL_TO", "").strip(),
                "render_url": os.getenv("RENDER_DEMO_URL", "").strip(),
                "database_url": bool(os.getenv("DATABASE_URL", "").strip()),
                "backups": backups,
                "session_count": session_count,
                "key_count": len(ctx["pool"]["key_list"]),
                "version": "1.0.0",
            }
        )
        return _render(request, "settings.html", ctx)


@app.post("/settings/password")
def settings_password(
    request: Request,
    current: str = Form(...),
    new: str = Form(...),
    confirm: str = Form(...),
    csrf_token: str = Form(""),
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        user = conn.execute("SELECT * FROM admin_users WHERE id = 1").fetchone()
        if not user or not auth.verify_password(current, user["password_hash"]):
            return _redirect("/settings", conn, session, "Current password is incorrect.", "error")
        problem = auth.password_problem(new)
        if problem:
            return _redirect("/settings", conn, session, problem, "error")
        if new != confirm:
            return _redirect("/settings", conn, session, "New passwords do not match.", "error")
        conn.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = 1",
            (auth.hash_password(new), db.now_iso()),
        )
        removed = auth.delete_other_sessions(conn, session["token_hash"])
        _audit(conn, request, "password_changed", details=f"{removed} other session(s) signed out")
        conn.commit()
        return _redirect(
            "/settings",
            conn,
            session,
            f"Password changed. {removed} other session(s) signed out.",
        )


@app.post("/settings/model")
def settings_model(
    request: Request, model: str = Form(...), csrf_token: str = Form("")
):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        model = model.strip()
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,100}$", model):
            return _redirect("/settings", conn, session, "That model ID looks invalid.", "error")
        db.set_setting(conn, "groq_model", model)
        _audit(conn, request, "model_changed", details=model)
        conn.commit()
        return _redirect("/settings", conn, session, "Groq model updated for the local bot.")


@app.post("/settings/backup")
def settings_backup(request: Request, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        db.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        target = db.BACKUP_DIR / f"admin-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        source = db.connect()
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        _audit(conn, request, "backup_created", details=target.name)
        conn.commit()
        return _redirect("/settings", conn, session, f"Backup created: {target.name}")


@app.post("/settings/logout-all")
def settings_logout_all(request: Request, csrf_token: str = Form("")):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _check_csrf(request, session, csrf_token)
        removed = auth.delete_other_sessions(conn, session["token_hash"])
        _audit(conn, request, "logout_other_sessions", details=f"{removed} session(s)")
        conn.commit()
        return _redirect("/settings", conn, session, f"Signed out {removed} other session(s).")


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------

def _csv_safe(value):
    """Prevent spreadsheet formula injection from customer-supplied text."""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


def _csv_response(rows: list[dict], headers: list[str], filename: str) -> Response:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        record = dict(row)
        writer.writerow({header: _csv_safe(record.get(header, "")) for header in headers})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/conversations.csv")
def export_conversations(request: Request, days: str = "30", client: str = ""):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        _audit(conn, request, "export_conversations", client or "all", f"{days} days")
        conn.commit()
    rows = metrics.export_rows(_page_int(days, 30, 1, 365), client or None)
    return _csv_response(
        rows,
        ["id", "timestamp", "client_id", "session_id", "query", "response", "escalated", "escalation_reason"],
        f"conversations-{date.today().isoformat()}.csv",
    )


@app.get("/export/clients.csv")
def export_clients(request: Request):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        rows = _client_rows(conn)
        _audit(conn, request, "export_clients", "all", f"{len(rows)} rows")
        conn.commit()
    return _csv_response(
        rows,
        ["id", "display_name", "domain", "status", "contact_name", "contact_email", "plan_name", "sub_status"],
        f"clients-{date.today().isoformat()}.csv",
    )


@app.get("/export/invoices.csv")
def export_invoices(request: Request):
    with db.db() as conn:
        session = auth.get_session(request, conn)
        if not session:
            raise auth.AuthRequired()
        rows = billing.list_invoices(conn)
        _audit(conn, request, "export_invoices", "all", f"{len(rows)} rows")
        conn.commit()
    return _csv_response(
        rows,
        ["number", "display_name", "issued_on", "due_on", "amount_cents", "currency", "effective_status", "paid_on", "method", "notes"],
        f"invoices-{date.today().isoformat()}.csv",
    )


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

@app.get("/healthz", include_in_schema=False)
def healthz():
    return JSONResponse({"status": "ok", "app": "vizara-operator-console"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
