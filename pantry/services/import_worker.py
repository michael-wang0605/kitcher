"""Short database claims; inference runs outside transactions in a bounded child."""

import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from pantry.models import ImportDraft, ImportJob, WorkerStatus
from pantry.services import candidates, imports
from pantry.services.quantities import DomainError

LEASE_SECONDS = 300
DEADLINE_SECONDS = 120
RETRY_DELAYS = {1: 10, 2: 30}


def failure_candidate(draft, error):
    return [
        {
            "name": "Unresolved image",
            "ingredient": None,
            "kind": "set" if draft.source_type == "fridge" else "add",
            "amount": "",
            "unit": "",
            "reconcile": "",
            "evidence": error[:1000],
            "unresolved": "Enter visible facts manually or retry local recognition.",
        }
    ]


def claim(now=None, *, draft_id=None):
    now = now or timezone.now()
    with transaction.atomic():
        eligible = Q(state="queued", next_attempt_at__lte=now) | Q(
            state="processing", lease_until__lte=now
        )
        jobs = ImportJob.objects.filter(eligible)
        if draft_id is not None:
            jobs = jobs.filter(draft_id=draft_id)
        for job in jobs.select_related("draft").order_by("created_at")[:50]:
            if job.owner_id != job.draft.owner_id or job.draft.status != "processing":
                ImportJob.objects.filter(pk=job.pk).update(
                    state="cancelled", attempt_token=None, lease_until=None
                )
                continue
            if not imports.source_available(job, now):
                expire(job, now)
                continue
            if job.attempts >= 3:
                terminal_failure(
                    job, "Local processing stopped after three attempts.", now
                )
                continue
            token = uuid.uuid4()
            changed = (
                ImportJob.objects.filter(pk=job.pk)
                .filter(eligible)
                .update(
                    state="processing",
                    attempts=F("attempts") + 1,
                    attempt_token=token,
                    lease_until=now + timedelta(seconds=LEASE_SECONDS),
                    claimed_revision=job.draft.revision,
                    error="",
                )
            )
            if changed == 1:
                job.refresh_from_db()
                return job
    return None


def expire(job, now):
    # Caller holds a short transaction. Reviewed structured data survives source deletion.
    ImportDraft.objects.filter(pk=job.draft_id, status="processing").update(
        status="expired",
        revision=F("revision") + 1,
        error="The photo expired. Upload it again or enter stock manually.",
        updated_at=now,
    )
    ImportJob.objects.filter(pk=job.pk).exclude(
        state__in=["succeeded", "cancelled"]
    ).update(
        state="expired",
        attempt_token=None,
        lease_until=None,
        error="Source unavailable or expired.",
    )
    transaction.on_commit(lambda: imports.delete_source(job.source_name))


def terminal_failure(job, error, now):
    ImportDraft.objects.filter(pk=job.draft_id, status="processing").update(
        status="needs_review",
        revision=F("revision") + 1,
        error=error,
        candidates=failure_candidate(job.draft, error),
        updated_at=now,
    )
    ImportJob.objects.filter(pk=job.pk).update(
        state="failed", attempt_token=None, lease_until=None, error=error
    )


def publish(job, *, raw=None, error="", transient=False, metadata=None, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        draft = (
            ImportDraft.objects.select_for_update()
            .filter(pk=job.draft_id, owner_id=job.owner_id)
            .first()
        )
        current = ImportJob.objects.filter(
            pk=job.pk,
            owner_id=job.owner_id,
            state="processing",
            attempt_token=job.attempt_token,
            lease_until__gt=now,
            claimed_revision=job.claimed_revision,
        ).first()
        if (
            current is None
            or draft is None
            or draft.status != "processing"
            or draft.revision != job.claimed_revision
        ):
            return False
        if not imports.source_available(current, now):
            expire(current, now)
            return False
        if not error:
            try:
                values = candidates.validate_raw(draft.owner, raw, draft.source_type)
            except DomainError as exc:
                error, transient = str(exc), False
        if error and transient and current.attempts < 3:
            ImportJob.objects.filter(
                pk=current.pk, attempt_token=current.attempt_token
            ).update(
                state="queued",
                attempt_token=None,
                lease_until=None,
                next_attempt_at=now + timedelta(seconds=RETRY_DELAYS[current.attempts]),
                error=error,
                metadata=metadata or {},
            )
            return True
        if error:
            terminal_failure(current, error, now)
            ImportJob.objects.filter(pk=current.pk).update(metadata=metadata or {})
            return True
        ImportDraft.objects.filter(
            pk=draft.pk, revision=job.claimed_revision, status="processing"
        ).update(
            candidates=values,
            status="needs_review",
            error="",
            revision=F("revision") + 1,
            updated_at=now,
        )
        ImportJob.objects.filter(
            pk=current.pk, attempt_token=current.attempt_token
        ).update(
            state="succeeded",
            attempt_token=None,
            lease_until=None,
            error="",
            metadata=metadata or {},
        )
        return True


def interpret(job):
    command = [
        sys.executable,
        "-m",
        "pantry.interpretation.runner",
        str(imports.source_path(job.source_name)),
        job.draft.source_type,
    ]
    environment = os.environ.copy()
    environment["OLLAMA_NO_CLOUD"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=DEADLINE_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": "Local recognition exceeded its 120-second limit.",
            "transient": True,
        }
    except OSError:
        return {
            "error": "Local recognition could not start. Enter facts manually.",
            "transient": True,
        }
    if len(result.stdout) > 2_000_000:
        return {
            "error": "Recognition output exceeded the supported size.",
            "transient": False,
        }
    try:
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or set(response) - {
            "raw",
            "error",
            "transient",
            "metadata",
        }:
            raise ValueError
        if (
            not isinstance(response.get("error", ""), str)
            or len(response.get("error", "")) > 1000
        ):
            raise ValueError
        if type(response.get("transient", False)) is not bool or not isinstance(
            response.get("metadata", {}), dict
        ):
            raise ValueError
        if result.returncode != 0 and not response.get("error"):
            raise ValueError
        return response
    except (ValueError, TypeError):
        return {
            "error": "Recognition returned malformed output. Enter facts manually.",
            "transient": False,
        }


def process_one():
    job = claim()
    if job is None:
        return False
    publish(job, **interpret(job))
    return True


def cleanup(now=None):
    now = now or timezone.now()
    removed = 0
    delayed = 0
    sources = Q(expires_at__lte=now) | Q(draft__status__in=["applied", "cancelled"])
    for job in ImportJob.objects.filter(sources).select_related("draft"):
        with transaction.atomic():
            if job.expires_at <= now and job.state in {
                "queued",
                "processing",
                "failed",
            }:
                expire(job, now)
        path = imports.source_path(job.source_name)
        if path.exists():
            if imports.delete_source(job.source_name):
                removed += 1
            else:
                delayed += 1
    # Orphans are removed only after a grace period to avoid racing an upload write.
    referenced = set(ImportJob.objects.values_list("source_name", flat=True))
    cutoff = (now - timedelta(minutes=15)).timestamp()
    for path in imports.spool().glob("*.jpg"):
        if path.name not in referenced and path.stat().st_mtime < cutoff:
            if imports.delete_source(path.name):
                removed += 1
            else:
                delayed += 1
    WorkerStatus.objects.update_or_create(
        name="cleanup",
        defaults={
            "last_seen": now,
            "detail": f"Private source cleanup delayed for {delayed} file(s); access is denied after cancellation, confirmation, or expiry."
            if delayed
            else "Private source cleanup completed.",
        },
    )
    return removed
