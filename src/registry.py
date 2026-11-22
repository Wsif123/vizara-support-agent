"""Config-driven client registry.

The public bot no longer keeps a hardcoded allowlist. Dropping a new
`config/client_<id>.yaml` file (with `enabled: true`) is enough to register a
tenant after the next restart. The dashboard writes these files for you.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_CACHE_TTL = 2.0
_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cached_at = 0.0


def _load() -> dict[str, dict]:
    clients: dict[str, dict] = {}
    for path in sorted(CONFIG_DIR.glob("client_*.yaml")):
        client_id = path.name[len("client_") : -len(".yaml")]
        try:
            with open(path, encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            config = raw.get("client") or {}
        except Exception as exc:
            print(f"[registry] skipping {path.name}: {type(exc).__name__}")
            continue
        clients[client_id] = {
            "id": client_id,
            "enabled": bool(config.get("enabled", True)),
            "billing_status": str(
                config.get("billing_status") or "active"
            ).strip().lower(),
            "domain": (config.get("domain") or "").strip().lower() or None,
            "display_name": config.get("display_name") or client_id,
            "config": config,
        }
    return clients


def all_clients() -> dict[str, dict]:
    global _cache, _cached_at
    now = time.monotonic()
    with _lock:
        if _cache is None or now - _cached_at > _CACHE_TTL:
            _cache = _load()
            _cached_at = now
        return _cache


def valid_client_ids() -> set[str]:
    return {
        cid
        for cid, item in all_clients().items()
        if item["enabled"] and item["billing_status"] != "suspended"
    }


def client_domains() -> dict[str, str | None]:
    return {
        cid: item["domain"]
        for cid, item in all_clients().items()
        if item["enabled"]
    }


def get_client(client_id: str) -> dict | None:
    item = all_clients().get(client_id)
    if not item or not item["enabled"]:
        return None
    return item


def invalidate_cache() -> None:
    global _cache, _cached_at
    with _lock:
        _cache = None
        _cached_at = 0.0
