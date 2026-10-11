"""Private import drafts, versioned edits, and inventory confirmation context."""

import hashlib
import io
import logging
import uuid
import warnings
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from pantry.models import ImportDraft, ImportJob, Ingredient
from pantry.services import candidates, inventory
from pantry.services.quantities import Conflict, DomainError, quantity

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000


def spool():
    path = Path(settings.VAR_DIR) / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def source_path(name):
    if not name or Path(name).name != name or not name.endswith(".jpg"):
        raise DomainError("Invalid private source path.")
    path = (spool() / name).resolve()
    if path.parent != spool():
        raise DomainError("Invalid private source path.")
    return path


def delete_source(name):
    if name:
        try:
            source_path(name).unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning(
                "Private source deletion delayed; cleanup will retry."
            )
            return False
    return True


def source_available(job, now=None):
    return bool(
        job.source_name
        and job.expires_at > (now or timezone.now())
        and source_path(job.source_name).is_file()
    )


def create_text(owner, text):
    return ImportDraft.objects.create(
        owner=owner,
        source_type="text",
        source_text=text,
        candidates=candidates.parse_text(owner, text),
        status="needs_review",
    )


def create_photo(owner, upload, source_type):
    if source_type not in {"fridge", "receipt"}:
        raise DomainError("Choose fridge or receipt.")
    if upload.size > MAX_BYTES:
        raise DomainError("Upload one JPEG or PNG no larger than 10 MiB.")
    content = upload.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise DomainError("Upload one JPEG or PNG no larger than 10 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                # Phone JPEGs may contain an MPF auxiliary gain/depth image.
                # Decode only their primary JPEG frame and discard auxiliary frames.
                jpeg = image.format in {"JPEG", "MPO"} and content.startswith(
                    b"\xff\xd8"
                )
                if not jpeg and image.format != "PNG":
                    raise DomainError(
                        "Use JPEG or PNG. Export HEIC photos as JPEG first."
                    )
                if image.width * image.height > MAX_PIXELS:
                    raise DomainError("The photo exceeds 25,000,000 decoded pixels.")
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise DomainError(
            "The image cannot be decoded safely. Use a smaller JPEG or PNG."
        ) from exc
    fingerprint = hashlib.sha256(content).hexdigest()
    duplicate = ImportDraft.objects.filter(
        owner=owner, fingerprint=fingerprint
    ).exists()
    name = f"{uuid.uuid4().hex}.jpg"
    path = source_path(name)
    try:
        normalized.save(path, format="JPEG", quality=90)
        with transaction.atomic():
            draft = ImportDraft.objects.create(
                owner=owner,
                source_type=source_type,
                fingerprint=fingerprint,
                duplicate_source=duplicate,
                status="processing",
            )
            ImportJob.objects.create(
                owner=owner,
                draft=draft,
                source_name=name,
                fingerprint=fingerprint,
                expires_at=timezone.now() + timedelta(hours=24),
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return draft


def get_draft(owner, pk):
    draft = ImportDraft.objects.filter(owner=owner, pk=pk).first()
    if draft is None:
        raise DomainError("Import draft not found.")
    return draft


def validate_edits(owner, source_type, rows):
    if not rows or len(rows) > 200:
        raise DomainError("Review between one and 200 observations.")
    catalog = {i.pk: i for i in Ingredient.objects.filter(owner=owner)}
    result = []
    for row in rows:
        if row.get("remove"):
            continue
        ingredient = catalog.get(row.get("ingredient"))
        kind = {"fridge": "set", "receipt": "add"}.get(source_type, row.get("kind"))
        if ingredient is None or kind not in {"set", "add", "subtract"}:
            raise DomainError(
                "Choose an ingredient and operation for every retained observation."
            )
        amount, unit = str(row.get("amount", "")).strip(), row.get("unit")
        quantity(amount, unit, ingredient.dimension, allow_zero=kind != "add")
        reconcile = str(row.get("reconcile", "")).strip()
        if reconcile:
            quantity(reconcile, ingredient.unit, ingredient.dimension)
        result.append(
            {
                "ingredient": ingredient.pk,
                "name": ingredient.name,
                "kind": kind,
                "amount": amount,
                "unit": unit,
                "reconcile": reconcile,
                "unresolved": "",
                "evidence": str(row.get("evidence", ""))[:1000],
            }
        )
    if not result:
        raise DomainError("Keep at least one observation, or cancel this draft.")
    return result


def edit(owner, pk, revision, rows, *, duplicate_ack=False):
    with transaction.atomic():
        draft = get_draft(owner, pk)
        if draft.status not in {"needs_review", "processing"}:
            raise DomainError("This draft cannot be edited.")
        if draft.revision != revision:
            raise Conflict(
                "The draft changed. Reload and review its current observations."
            )
        values = validate_edits(owner, draft.source_type, rows)
        changed = ImportDraft.objects.filter(
            pk=draft.pk, owner=owner, revision=revision, status=draft.status
        ).update(
            candidates=values,
            status="needs_review",
            revision=F("revision") + 1,
            error="",
            duplicate_ack=bool(duplicate_ack),
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise Conflict("The draft changed. Reload before editing.")
        ImportJob.objects.filter(
            draft=draft, state__in=["queued", "processing"]
        ).update(
            state="cancelled",
            attempt_token=None,
            lease_until=None,
            error="Replaced by manual review.",
        )
        draft.refresh_from_db()
        return draft


def operations(owner, draft):
    if (
        draft.status != "needs_review"
        or not draft.candidates
        or any(row.get("unresolved") for row in draft.candidates)
    ):
        raise DomainError("Resolve every observation before previewing inventory.")
    if draft.duplicate_source and not draft.duplicate_ack:
        raise DomainError(
            "Confirm that this repeated source represents a new inventory change."
        )
    values = validate_edits(owner, draft.source_type, draft.candidates)
    catalog = {i.pk: i for i in Ingredient.objects.filter(owner=owner)}
    return [
        inventory.operation(
            catalog[row["ingredient"]],
            row["kind"],
            quantity(
                row["amount"],
                row["unit"],
                catalog[row["ingredient"]].dimension,
                allow_zero=row["kind"] != "add",
            ),
            reconcile=quantity(
                row["reconcile"],
                catalog[row["ingredient"]].unit,
                catalog[row["ingredient"]].dimension,
            )
            if row["reconcile"]
            else None,
        )
        for row in values
    ]


def preview(owner, pk):
    with transaction.atomic():
        draft = get_draft(owner, pk)
        return inventory.preview(
            owner,
            operations(owner, draft),
            source=draft.source_type,
            source_input={
                "draft": str(draft.pk),
                "text": draft.source_text,
                "fingerprint": draft.fingerprint,
                "observations": draft.candidates,
            },
            context={
                "kind": "import",
                "draft": str(draft.pk),
                "revision": draft.revision,
            },
        )


def check_context(owner, context, supplied_operations):
    draft = (
        ImportDraft.objects.select_for_update()
        .filter(owner=owner, pk=context.get("draft"))
        .first()
    )
    if draft is None:
        raise DomainError("Import draft not found.")
    if draft.revision != context.get("revision") or draft.status != "needs_review":
        raise Conflict("Import changed after your preview. Review the current draft.")
    if operations(owner, draft) != supplied_operations:
        raise DomainError("Import operations do not match the reviewed draft.")
    return draft


def finish(owner, context, event, supplied_operations):
    draft = check_context(owner, context, supplied_operations)
    changed = ImportDraft.objects.filter(
        pk=draft.pk, owner=owner, revision=draft.revision, status="needs_review"
    ).update(
        status="applied",
        applied_event=event,
        revision=F("revision") + 1,
        updated_at=timezone.now(),
    )
    if changed != 1:
        raise Conflict("Import changed after your preview.")
    job = ImportJob.objects.filter(draft=draft, owner=owner).first()
    if job:
        ImportJob.objects.filter(pk=job.pk).update(attempt_token=None, lease_until=None)
        transaction.on_commit(lambda: delete_source(job.source_name))


def cancel(owner, pk, revision):
    with transaction.atomic():
        draft = get_draft(owner, pk)
        if draft.status not in {"processing", "needs_review"}:
            raise DomainError("This draft cannot be cancelled.")
        if (
            ImportDraft.objects.filter(
                pk=draft.pk, revision=revision, owner=owner
            ).update(
                status="cancelled",
                revision=F("revision") + 1,
                updated_at=timezone.now(),
            )
            != 1
        ):
            raise Conflict("The draft changed. Reload before cancelling.")
        job = ImportJob.objects.filter(draft=draft, owner=owner).first()
        if job:
            ImportJob.objects.filter(pk=job.pk).update(
                state="cancelled", attempt_token=None, lease_until=None
            )
            transaction.on_commit(lambda: delete_source(job.source_name))


def retry(owner, pk, revision):
    with transaction.atomic():
        draft = get_draft(owner, pk)
        job = ImportJob.objects.filter(owner=owner, draft=draft).first()
        if (
            draft.status != "needs_review"
            or job is None
            or job.state not in {"failed", "cancelled"}
        ):
            raise DomainError("This import cannot be retried.")
        if not source_available(job):
            raise DomainError("The source expired or was removed. Upload it again.")
        if (
            ImportDraft.objects.filter(
                pk=draft.pk, revision=revision, owner=owner
            ).update(
                status="processing",
                candidates=[],
                error="",
                revision=F("revision") + 1,
                updated_at=timezone.now(),
            )
            != 1
        ):
            raise Conflict("The draft changed. Reload before retrying.")
        ImportJob.objects.filter(pk=job.pk).update(
            state="queued",
            attempts=0,
            attempt_token=None,
            lease_until=None,
            next_attempt_at=timezone.now(),
            error="",
        )
