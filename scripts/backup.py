#!/usr/bin/env python3
"""Back up the local operator data to backups/<UTC timestamp>/.

Required (abort if missing):
    admin/admin.db

Optional (warn and continue if missing):
    admin/.secret.key
    logs/conversations.db
    config/
    data/

SQLite databases are snapshotted through SQLite's backup API rather than copied
as raw files. Both databases run in WAL mode, so a plain copy without the -wal
sidecar could omit recent commits.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = PROJECT_ROOT / "backups"

REQUIRED_DB = Path("admin") / "admin.db"
OPTIONAL_FILES = (
    Path("admin") / ".secret.key",
    Path("logs") / "conversations.db",
)
OPTIONAL_DIRS = (Path("config"), Path("data"))
ALL_SOURCES = (REQUIRED_DB, *OPTIONAL_FILES, *OPTIONAL_DIRS)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_destination() -> Path:
    """Return a timestamped backup folder that does not already exist."""
    base = BACKUP_ROOT / utc_timestamp()
    destination = base
    counter = 1
    while destination.exists():
        destination = base.with_name(f"{base.name}-{counter}")
        counter += 1
    return destination


def snapshot_sqlite(source: Path, target: Path) -> None:
    """Write a consistent standalone copy, including data still in the WAL."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    try:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
            integrity = target_conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            target_conn.close()
    finally:
        source_conn.close()

    if not integrity or integrity[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed for {target}")


def copy_source(relative: Path, destination: Path) -> None:
    source = PROJECT_ROOT / relative
    target = destination / relative

    if source.is_dir():
        shutil.copytree(source, target)
        files = [path for path in target.rglob("*") if path.is_file()]
        size = sum(path.stat().st_size for path in files)
        print(f"  copied {relative}{chr(92)} ({len(files)} files, {size} bytes)")
        return

    if source.suffix.lower() == ".db":
        snapshot_sqlite(source, target)
        print(
            f"  copied {relative} "
            f"({target.stat().st_size} bytes, consistent SQLite snapshot)"
        )
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"  copied {relative} ({target.stat().st_size} bytes)")


def main() -> int:
    required_path = PROJECT_ROOT / REQUIRED_DB
    if not required_path.is_file():
        print(f"ERROR: required source is missing: {required_path}", file=sys.stderr)
        print(
            "ERROR: backup aborted because admin/admin.db (the ledger) cannot be lost.",
            file=sys.stderr,
        )
        return 1

    destination = new_destination()

    try:
        destination.mkdir(parents=True)
    except OSError as exc:
        print(f"ERROR: could not create {destination}: {exc}", file=sys.stderr)
        return 2

    print("Vizara backup")
    print(f"Source root: {PROJECT_ROOT}")
    print(f"Backup destination: {destination}")
    print()

    copied = 0
    warnings = 0

    for relative in ALL_SOURCES:
        source = PROJECT_ROOT / relative
        if not source.exists():
            print(f"WARN: missing optional source, skipped: {relative}")
            warnings += 1
            continue

        try:
            copy_source(relative, destination)
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            print(f"ERROR: failed to copy {relative}: {exc}", file=sys.stderr)
            return 2
        copied += 1

    print()
    if warnings:
        print(f"Backup finished with {warnings} warning(s); {copied} source(s) copied.")
    else:
        print(f"Backup complete: {copied} of {len(ALL_SOURCES)} sources copied.")

    print()
    print("Restore command (stop the operator console first):")
    print(f'  robocopy "{destination}" "{PROJECT_ROOT}" /E /COPY:DAT')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
