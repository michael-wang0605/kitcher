"""Verified SQLite online backups. No live database replacement is supported."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from pantry.services.process_lock import process_lock
from pantry.services.quantities import DomainError

DAILY_NAME = re.compile(r"kitcher-\d{4}-\d{2}-\d{2}\.sqlite3\Z")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_database(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise DomainError("Backup database integrity check failed.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DomainError("Backup contains broken database references.")
        migrations = db.execute(
            "SELECT app, name FROM django_migrations ORDER BY app, name"
        ).fetchall()
    return {
        "sha256": digest(path),
        "bytes": path.stat().st_size,
        "migrations": migrations,
    }


def snapshot(source, target):
    """Build and verify a temporary copy before publishing the requested path."""
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if source == target:
        raise DomainError("A backup cannot replace its source database.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    deadline = time.monotonic() + 120

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise DomainError("Database backup exceeded its two-minute deadline.")

    try:
        with (
            closing(
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as original,
            closing(sqlite3.connect(temporary, timeout=5)) as copied,
        ):
            original.backup(copied, pages=256, progress=progress, sleep=0.05)
        result = inspect_database(temporary)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return result
    finally:
        temporary.unlink(missing_ok=True)


def mirror_backup(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        with source.open("rb") as original, temporary.open("xb") as copied:
            shutil.copyfileobj(original, copied)
            copied.flush()
            os.fsync(copied.fileno())
        if digest(temporary) != expected:
            raise DomainError("Mirror checksum differs from the verified backup.")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def prune_daily(directory):
    directory = Path(directory).resolve()
    candidates = sorted(
        (
            p
            for p in directory.iterdir()
            if DAILY_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()
        ),
        key=lambda p: p.name,
        reverse=True,
    )
    for path in candidates[7:]:
        if path.resolve().parent != directory:
            raise DomainError("Backup retention target escaped its directory.")
        path.unlink()
        path.with_suffix(".json").unlink(missing_ok=True)


def create_backup(*, pre_migration=False):
    if connection.vendor != "sqlite" or connection.in_atomic_block:
        raise DomainError("Run SQLite backup outside an application transaction.")
    root = Path(settings.BACKUP_DIR).resolve()
    mirror = Path(settings.BACKUP_MIRROR).resolve() if settings.BACKUP_MIRROR else None
    if mirror and (
        mirror == root or mirror.is_relative_to(root) or root.is_relative_to(mirror)
    ):
        raise DomainError("Backup mirror must use a separate directory tree.")
    kind = "pre-migration" if pre_migration else "daily"
    now = timezone.now()
    name = (
        f"kitcher-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3"
        if pre_migration
        else f"kitcher-{now:%Y-%m-%d}.sqlite3"
    )
    target = root / kind / name
    status_path = Path(settings.VAR_DIR) / "backup-status.json"
    with process_lock(
        "backup.lock", "A backup is already running for this data directory."
    ):
        previous = read_status()
        status = {
            **previous,
            "last_attempt": now.isoformat(),
            "error": "",
            "mirror_error": "",
        }
        try:
            result = snapshot(connection.settings_dict["NAME"], target)
            manifest = {
                "format": "kitcher.sqlite-backup",
                "schema_version": 1,
                "created_at": now.isoformat(),
                "kind": kind,
                "file": name,
                **result,
            }
            atomic_json(target.with_suffix(".json"), manifest)
            status.update(
                last_success=now.isoformat(), path=str(target), sha256=result["sha256"]
            )
            if mirror:
                mirror_target = mirror / kind / name
                try:
                    mirror_backup(target, mirror_target, result["sha256"])
                    atomic_json(mirror_target.with_suffix(".json"), manifest)
                    status.update(
                        mirror_success=now.isoformat(), mirror_path=str(mirror_target)
                    )
                    if not pre_migration:
                        prune_daily(mirror / kind)
                except (OSError, DomainError) as exc:
                    status["mirror_error"] = (
                        type(exc).__name__ + ": verified mirror did not complete."
                    )
            else:
                status["mirror_error"] = "No separate backup mirror configured."
            if not pre_migration:
                prune_daily(root / kind)
            atomic_json(status_path, status)
            return status
        except Exception as exc:
            status["error"] = type(exc).__name__ + ": local backup did not complete."
            atomic_json(status_path, status)
            raise


def read_status():
    try:
        value = json.loads(
            (Path(settings.VAR_DIR) / "backup-status.json").read_text(encoding="utf-8")
        )
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def restore_backup(source, destination):
    """Restore only to a new file; caller must select that separate data directory."""
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination.exists():
        raise DomainError(
            "Restore destination already exists. Choose a fresh database path."
        )
    try:
        manifest = json.loads(source.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainError("A valid adjacent backup manifest is required.") from exc
    if not isinstance(manifest, dict):
        raise DomainError("Invalid backup manifest structure.")
    actual = inspect_database(source)
    if (
        manifest.get("format") != "kitcher.sqlite-backup"
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("sha256") != actual["sha256"]
        or manifest.get("bytes") != actual["bytes"]
        or manifest.get("migrations") != [list(row) for row in actual["migrations"]]
    ):
        raise DomainError(
            "Backup verification failed; its manifest or checksum differs."
        )
    from django.db.migrations.loader import MigrationLoader

    available = set(MigrationLoader(None).disk_migrations)
    if not set(map(tuple, actual["migrations"])).issubset(available):
        raise DomainError(
            "Backup needs migration code that is not installed. Restore with its matching code version."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation makes concurrent restore attempts refuse this destination.
    with destination.open("xb"):
        pass
    try:
        # This source is a sealed verified backup, not the live database. A
        # checksum-checked byte copy also refuses a backup replaced during restore.
        mirror_backup(source, destination, actual["sha256"])
        copied = inspect_database(destination)
        return {"path": str(destination), "sha256": copied["sha256"], "integrity": "ok"}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
