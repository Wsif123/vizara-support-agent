#!/usr/bin/env python3
"""Promote one client's config and source data to the deployed branch.

Usage:
    py -3.11 scripts/promote_client.py <client_id>

The script intentionally refuses to run when the working tree contains changes
outside config/ and data/, so a promotion cannot accidentally ship unrelated
work. It reads only the client's name from the generated YAML, so it uses the
Python standard library instead of importing PyYAML.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DEFAULT_RENDER_URL = "https://vizara-support-agent.onrender.com"
CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )


def read_client_name(config_path: Path) -> str | None:
    """Return the client.name value from the config, or None when absent."""
    in_client = False
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_client = line.strip() == "client:"
            continue
        if not in_client:
            continue
        match = re.match(r"^  name:\s*(.+?)\s*$", line)
        if match:
            value = match.group(1).strip().strip("'\"")
            if value and value.lower() not in {"null", "~"}:
                return value
    return None


def changed_paths() -> list[str]:
    """Parse `git status --porcelain` (NUL-separated so spaces are safe)."""
    result = git("status", "--porcelain=v1", "-z")
    if result.returncode != 0:
        fail(result.stderr.strip() or "git status failed.")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:] if len(entry) > 3 else ""
        if "R" in status or "C" in status:
            # Rename/copy entries carry the original path in the next record.
            if index < len(entries):
                index += 1
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def is_allowed(path: str) -> bool:
    return (
        path == "config"
        or path.startswith("config/")
        or path == "data"
        or path.startswith("data/")
    )


def staged_paths() -> list[str]:
    result = git("diff", "--cached", "--name-only", "-z")
    if result.returncode != 0:
        fail(result.stderr.strip() or "Could not inspect staged changes.")
    return [path for path in result.stdout.split("\0") if path]


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        fail("Usage: py -3.11 scripts/promote_client.py <client_id>")

    client_id = argv[1].strip()
    if not CLIENT_ID_RE.match(client_id):
        fail(
            "Client ID must be 1–64 characters: lowercase letters, numbers, - or _."
        )

    config_rel = f"config/client_{client_id}.yaml"
    config_path = ROOT / config_rel
    if not config_path.is_file():
        fail(f"Config not found: {config_rel}")

    name = read_client_name(config_path)
    if name is None:
        fail(f"No client.name in {config_rel}")
    if (
        not name.strip()
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
    ):
        fail(f"Unsafe client name in {config_rel}: {name!r}")
    data_rel = f"data/{name}"
    data_path = ROOT / data_rel
    if not data_path.is_dir():
        fail(f"Data directory not found: {data_rel}")

    print(f"Config: {config_path.resolve()}")
    print(f"Data:   {data_path.resolve()}")

    offending = [path for path in changed_paths() if not is_allowed(path)]
    if offending:
        print("Refusing to promote: changed paths outside config/ and data/:")
        for path in offending:
            print(f"  {path}")
        return 1

    result = git("add", "--", config_rel, data_rel)
    if result.returncode != 0:
        fail(result.stderr.strip() or "git add failed.")

    staged = staged_paths()
    target_prefix = f"{data_rel}/"
    unrelated = [
        path
        for path in staged
        if path != config_rel and not path.startswith(target_prefix)
    ]
    if unrelated:
        print("Refusing to commit staged paths outside this client:")
        for path in unrelated:
            print(f"  {path}")
        return 1

    if not staged:
        print(f"Nothing to promote for {client_id}")
        return 0

    print(f"Staged for {client_id}:")
    for path in staged:
        print(f"  {path}")

    result = git("commit", "-m", f"client: {client_id}")
    if result.returncode != 0:
        fail(result.stderr.strip() or result.stdout.strip() or "git commit failed.")
    print(result.stdout.strip())

    result = git("push")
    if result.returncode != 0:
        fail(result.stderr.strip() or result.stdout.strip() or "git push failed.")
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())

    render_url = (os.getenv("RENDER_DEMO_URL") or "").strip() or DEFAULT_RENDER_URL
    print(render_url)
    print("Deploy triggered — verify /health, then send one test message.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
