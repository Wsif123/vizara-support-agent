"""Client onboarding: config files, raw content, ingest, and admin records."""

from __future__ import annotations

import html
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from admin.db import now_iso

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}$")
ALLOWED_SUFFIXES = {".md", ".markdown", ".txt"}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024


def config_path(client_id: str) -> Path:
    return CONFIG_DIR / f"client_{client_id}.yaml"


def _read_yaml(client_id: str) -> dict:
    with open(config_path(client_id), encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return raw.get("client", {})


class ClientConfig(dict):
    """Client config with helpers used by the operator console templates."""

    def widget_snippet(self, client_id: str, bot_url: str) -> str:
        display_name = str(self.get("display_name") or client_id)
        escalation = self.get("escalation") or {}
        followup_name = (
            str(escalation.get("contact_name") or "").strip() or display_name
        )
        greeting = (
            f"Hi! I'm the {display_name} support assistant. How can I help you?"
        )
        base_url = str(bot_url).rstrip("/")
        attributes = [
            ("src", f"{base_url}/widget/chat-widget.js"),
            ("data-api-url", f"{base_url}/chat"),
            ("data-client-id", client_id),
            ("data-brand-name", display_name),
            ("data-followup-name", followup_name),
            ("data-greeting", greeting),
            ("data-header-sub", "Usually responds in minutes"),
            ("data-accent", "#7c3aed"),
        ]
        lines = [
            f'  {name}="{html.escape(str(value), quote=True)}"'
            for name, value in attributes
        ]
        return "<script\n" + "\n".join(lines) + "\n></script>"


def read_config(client_id: str) -> dict:
    return ClientConfig(_read_yaml(client_id))


def _write_yaml(client_id: str, client: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    target = config_path(client_id)
    text = (
        f"# {client.get('display_name', client_id)} — client configuration\n"
        "# Managed by the Vizara operator console.\n\n"
        + yaml.safe_dump(
            {"client": client},
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    )
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{client_id}.", suffix=".tmp", dir=str(CONFIG_DIR)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def normalize_domain(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    if not value:
        return None
    if len(value) > 253:
        raise ValueError("Domain is too long.")
    parsed = urlparse(value if "://" in value else f"//{value}")
    host = (parsed.hostname or "").strip(".")
    if not host or any(char in host for char in ["/", "\\", " "]):
        raise ValueError("Enter a domain such as example.com (no path).")
    if parsed.path not in ("", "/"):
        raise ValueError("Enter only the domain, not a path.")
    return host


def _sanitize_upload_name(name: str) -> str:
    base = Path(name or "").name
    if base != name or "\\" in (name or "") or not SAFE_FILE_RE.match(base):
        raise ValueError(f"Unsafe filename: {name!r}")
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Only .md, .markdown, and .txt files are supported.")
    return base


def _write_uploaded_files(raw_dir: Path, uploads) -> list[str]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    total = 0
    for upload in uploads or []:
        if not upload or not upload.filename:
            continue
        name = _sanitize_upload_name(upload.filename)
        data = upload.file.read()
        total += len(data)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"{name} is larger than 2 MB.")
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Uploads exceed the 10 MB total limit.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        (raw_dir / name).write_text(text.replace("\r\n", "\n"), encoding="utf-8")
        saved.append(name)
    return saved


def _raw_dir(client: dict) -> Path:
    return DATA_DIR / client["name"] / "raw"


def content_files(client: dict) -> list[dict]:
    raw = _raw_dir(client)
    files = []
    for name in client.get("source_files", []):
        path = raw / name
        if path.exists():
            stat = path.stat()
            files.append(
                {
                    "name": name,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(timespec="minutes"),
                    "exists": True,
                }
            )
        else:
            files.append(
                {"name": name, "size_kb": 0, "modified": None, "exists": False}
            )
    return files


def index_status(client_id: str, deep: bool = False) -> dict:
    client = _read_yaml(client_id)
    manifest_path = DATA_DIR / client["name"] / "processed" / "manifest.json"
    chunks = 0
    indexed_at = None
    sources = len(client.get("source_files", []))
    if manifest_path.exists():
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            chunks = int(manifest.get("chunks", 0))
        except Exception:
            chunks = 0
        indexed_at = datetime.fromtimestamp(
            manifest_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="minutes")
    stale = None
    if deep:
        try:
            from src.ingest import index_is_stale

            stale = bool(index_is_stale(client_id))
        except Exception:
            stale = None
    return {
        "chunks": chunks,
        "sources": sources,
        "indexed_at": indexed_at,
        "stale": stale,
    }


def _default_persona(display_name: str) -> str:
    return (
        f"You are a friendly and helpful support agent for {display_name}. "
        "Answer questions only based on the retrieved context. If the information "
        "is not in the context, do not guess — escalate to a human."
    )


def create_client(
    conn: sqlite3.Connection,
    client_id: str,
    display_name: str,
    domain: str | None,
    contact_name: str,
    contact_email: str,
    persona: str,
    top_k: int,
    score_threshold: float,
    uploads,
    pasted_content: str,
) -> dict:
    client_id = (client_id or "").strip().lower()
    display_name = (display_name or "").strip()
    if not SLUG_RE.match(client_id):
        raise ValueError(
            "Client ID must be 1–64 characters: lowercase letters, numbers, - or _."
        )
    if config_path(client_id).exists():
        raise ValueError("A client with that ID already exists.")
    if conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone():
        raise ValueError("A client with that ID already exists in the console.")
    if not display_name:
        raise ValueError("Display name is required.")
    if len(display_name) > 120:
        raise ValueError("Display name is too long.")
    domain = normalize_domain(domain)
    top_k = max(1, min(20, int(top_k or 6)))
    score_threshold = max(0.0, min(1.0, float(score_threshold or 0.5)))
    contact_email = (contact_email or "").strip()
    if contact_email and ("@" not in contact_email or len(contact_email) > 254):
        raise ValueError("Enter a valid contact email.")

    data_dir = DATA_DIR / client_id / "raw"
    saved = _write_uploaded_files(data_dir, uploads)
    pasted = (pasted_content or "").strip()
    if pasted:
        if len(pasted) > 500_000:
            raise ValueError("Pasted content is too large (500k characters max).")
        (data_dir / "knowledge.md").write_text(pasted + "\n", encoding="utf-8")
        saved.append("knowledge.md")
    if not saved:
        raise ValueError("Add at least one markdown file or paste some content.")

    client = {
        "name": client_id,
        "display_name": display_name,
        "domain": domain,
        "enabled": True,
        "source_files": sorted(set(saved)),
        "persona": (persona or "").strip() or _default_persona(display_name),
        "escalation": {
            "email": contact_email,
            "contact_name": (contact_name or "").strip() or display_name,
            "message": (
                f"Sorry, I can't answer that with confidence. Let me get a human "
                f"from the {display_name} team to help you out."
            ),
        },
        "retrieval": {"top_k": top_k, "score_threshold": round(score_threshold, 2)},
    }
    _write_yaml(client_id, client)

    ingest_error = None
    chunks = 0
    try:
        from src.ingest import ingest

        ingest(client_id)
        chunks = index_status(client_id)["chunks"]
    except Exception as exc:
        ingest_error = f"{type(exc).__name__}: index was not built. Use Reindex to retry."
        print(f"[onboarding] ingest failed for {client_id}: {type(exc).__name__}")

    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO clients
            (id, display_name, domain, status, contact_name, contact_email,
             notes, created_at, updated_at)
        VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
        """,
        (
            client_id,
            display_name,
            domain,
            (contact_name or "").strip() or None,
            contact_email or None,
            "Created by dashboard onboarding.",
            stamp,
            stamp,
        ),
    )
    conn.commit()
    return {
        "client_id": client_id,
        "display_name": display_name,
        "files": sorted(set(saved)),
        "chunks": chunks,
        "ingest_error": ingest_error,
    }


def update_client(
    client_id: str,
    display_name: str | None = None,
    domain: str | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    persona: str | None = None,
    top_k: int | None = None,
    score_threshold: float | None = None,
    enabled: bool | None = None,
) -> None:
    client = _read_yaml(client_id)
    if display_name is not None:
        display_name = display_name.strip()
        if not display_name or len(display_name) > 120:
            raise ValueError("Display name is required (120 characters max).")
        client["display_name"] = display_name
    if domain is not None:
        client["domain"] = normalize_domain(domain)
    if contact_name is not None:
        client.setdefault("escalation", {})["contact_name"] = contact_name.strip()[:120]
    if contact_email is not None:
        email = contact_email.strip()
        if email and ("@" not in email or len(email) > 254):
            raise ValueError("Enter a valid contact email.")
        client.setdefault("escalation", {})["email"] = email
    if persona is not None:
        persona = persona.strip()
        if len(persona) > 4000:
            raise ValueError("Persona is too long (4,000 characters max).")
        client["persona"] = persona or _default_persona(client.get("display_name", client_id))
    if top_k is not None:
        client.setdefault("retrieval", {})["top_k"] = max(1, min(20, int(top_k)))
    if score_threshold is not None:
        client.setdefault("retrieval", {})["score_threshold"] = round(
            max(0.0, min(1.0, float(score_threshold))), 2
        )
    if enabled is not None:
        client["enabled"] = bool(enabled)
    _write_yaml(client_id, client)


def replace_content(client_id: str, uploads) -> list[str]:
    client = _read_yaml(client_id)
    raw = _raw_dir(client)
    saved = _write_uploaded_files(raw, uploads)
    if not saved:
        raise ValueError("Choose at least one file.")
    sources = set(client.get("source_files", []))
    sources.update(saved)
    client["source_files"] = sorted(sources)
    _write_yaml(client_id, client)
    return sorted(sources)


def reindex(client_id: str) -> int:
    from src.ingest import ingest

    ingest(client_id)
    return index_status(client_id)["chunks"]


def list_configs() -> list[dict]:
    clients = []
    for path in sorted(CONFIG_DIR.glob("client_*.yaml")):
        client_id = path.name[len("client_") : -len(".yaml")]
        try:
            client = _read_yaml(client_id)
        except Exception:
            continue
        clients.append(
            {
                "id": client_id,
                "display_name": client.get("display_name", client_id),
                "domain": client.get("domain"),
                "enabled": client.get("enabled", True),
                "source_count": len(client.get("source_files", [])),
                "config": client,
            }
        )
    return clients


def sync_admin_clients(conn: sqlite3.Connection) -> int:
    """Seed dashboard client rows from existing YAML configs on first boot."""
    created = 0
    stamp = now_iso()
    for item in list_configs():
        exists = conn.execute(
            "SELECT id FROM clients WHERE id = ?", (item["id"],)
        ).fetchone()
        if exists:
            continue
        escalation = item["config"].get("escalation", {})
        conn.execute(
            """
            INSERT INTO clients
                (id, display_name, domain, status, contact_name, contact_email,
                 notes, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["display_name"],
                item["domain"],
                escalation.get("contact_name"),
                escalation.get("email"),
                "Imported from config on first run.",
                stamp,
                stamp,
            ),
        )
        created += 1
    conn.commit()
    return created
