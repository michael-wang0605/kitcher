"""One atomic write path for all reviewed inventory changes."""

import hashlib
import json
import uuid

from django.core import signing
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from pantry.models import (
    Ingredient,
    InventoryEvent,
    InventoryEventLine,
    PantryItem,
    PantryState,
)
from pantry.services.quantities import (
    Busy,
    Conflict,
    DomainError,
    display,
    validate_stored,
)

SALT = "kitcher.inventory.v1"


def operation(ingredient, kind, amount, *, reconcile=None):
    if kind not in {"set", "add", "subtract"}:
        raise DomainError("Unknown inventory operation.")
    validate_stored(amount, nullable=kind == "set")
    if kind == "add" and not amount:
        raise DomainError("Additions must be positive.")
    if reconcile is not None:
        validate_stored(reconcile)
    return {
        "ingredient": ingredient.pk,
        "kind": kind,
        "amount": amount,
        "reconcile": reconcile,
    }


def _calculate(owner, operations):
    if not operations or len(operations) > 200:
        raise DomainError("Review between one and 200 inventory changes.")
    ingredients = Ingredient.objects.filter(
        owner=owner, pk__in=[op.get("ingredient") for op in operations]
    )
    catalog = {ingredient.pk: ingredient for ingredient in ingredients}
    items = {
        item.ingredient_id: item
        for item in PantryItem.objects.filter(owner=owner, ingredient_id__in=catalog)
    }
    lines = {}
    for op in operations:
        ingredient = catalog.get(op.get("ingredient"))
        if ingredient is None:
            raise DomainError(
                "An ingredient is missing or belongs to a different account."
            )
        normalized = operation(
            ingredient, op.get("kind"), op.get("amount"), reconcile=op.get("reconcile")
        )
        if ingredient.pk not in lines:
            item = items.get(ingredient.pk)
            before = item.quantity if item else None
            lines[ingredient.pk] = {
                "ingredient": ingredient.pk,
                "name": ingredient.name,
                "unit": ingredient.unit,
                "dimension": ingredient.dimension,
                "before": before,
                "after": before,
                "version": item.version if item else 0,
                "operations": [],
            }
        line = lines[ingredient.pk]
        current = line["after"]
        kind, amount = normalized["kind"], normalized["amount"]
        if kind == "set":
            after = amount
        else:
            if normalized["reconcile"] is not None:
                current = normalized["reconcile"]
            if current is None:
                if kind == "subtract" and amount == 0:
                    line["operations"].append(normalized)
                    continue
                raise DomainError(
                    f"Confirm how much {ingredient.name} you had before this change."
                )
            after = current + amount if kind == "add" else current - amount
            if after < 0:
                raise DomainError(
                    f"Usage exceeds tracked {ingredient.name}. Reconcile current stock first."
                )
        validate_stored(after, nullable=True)
        line["after"] = after
        line["operations"].append(normalized)
    return list(lines.values())


def preview(owner, operations, *, source="manual", source_input=None, context=None):
    with transaction.atomic():
        state, _ = PantryState.objects.get_or_create(owner=owner)
        # SQLite serializes this short transaction; PostgreSQL locks the shared revision.
        state = PantryState.objects.select_for_update().get(pk=state.pk)
        lines = _calculate(owner, operations)
        if context:
            _context_fields(owner, context, operations)
        payload = {
            "schema": 1,
            "owner": owner.pk,
            "request_id": str(uuid.uuid4()),
            "revision": state.revision,
            "source": source,
            "source_input": source_input or {},
            "operations": operations,
            "versions": {str(line["ingredient"]): line["version"] for line in lines},
            "context": context or {},
            "review": _display_lines(lines),
        }
    return {
        "token": signing.dumps(payload, salt=SALT, compress=True),
        "payload": payload,
        "lines": _display_lines(lines),
    }


def _display_lines(lines):
    return [
        {
            **line,
            "before_display": display(line["before"]),
            "after_display": display(line["after"]),
        }
        for line in lines
    ]


def read_token(owner, token):
    try:
        payload = signing.loads(token, salt=SALT, max_age=86400)
    except signing.BadSignature as exc:
        raise DomainError(
            "This preview is invalid or expired. Create a new preview."
        ) from exc
    if payload.get("schema") != 1 or payload.get("owner") != owner.pk:
        raise DomainError("This preview belongs to a different account.")
    return payload


def command_hash(payload):
    data = {key: value for key, value in payload.items() if key != "request_id"}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def refresh(owner, token):
    payload = read_token(owner, token)
    if payload["context"].get("kind") == "cooking":
        from pantry.services.cooking import refresh as refresh_cooking

        return refresh_cooking(owner, payload)
    if payload["context"].get("kind") == "import":
        from pantry.services import imports

        return imports.preview(owner, payload["context"]["draft"])
    return preview(
        owner,
        payload["operations"],
        source=payload["source"],
        source_input=payload["source_input"],
        context=payload["context"],
    )


def confirm(owner, token):
    payload = read_token(owner, token)
    digest = command_hash(payload)
    try:
        with transaction.atomic():
            return _commit(owner, payload, digest)
    except (IntegrityError, OperationalError) as exc:
        # The failed transaction has exited. A racing identical request may have committed.
        try:
            existing = InventoryEvent.objects.filter(
                owner=owner, request_id=payload["request_id"]
            ).first()
        except OperationalError:
            existing = None
        if existing:
            if existing.command_hash != digest:
                raise DomainError(
                    "Request identifier was reused with different changes."
                ) from exc
            return existing
        raise Busy("Inventory is busy. Retry this same confirmation safely.") from exc


def _commit(owner, payload, digest):
    existing = InventoryEvent.objects.filter(
        owner=owner, request_id=payload["request_id"]
    ).first()
    if existing:
        if existing.command_hash != digest:
            raise DomainError("Request identifier was reused with different changes.")
        return existing
    claimed = PantryState.objects.filter(
        owner=owner, revision=payload["revision"]
    ).update(revision=F("revision") + 1)
    if claimed != 1:
        raise Conflict(
            "Inventory changed after your preview. Review the refreshed amounts."
        )
    lines = _calculate(owner, payload["operations"])
    if any(
        payload["versions"].get(str(line["ingredient"])) != line["version"]
        for line in lines
    ):
        raise Conflict("An ingredient changed after your preview.")
    extra = {}
    if payload["context"]:
        extra = _context_fields(owner, payload["context"], payload["operations"])
    event = InventoryEvent.objects.create(
        owner=owner,
        request_id=payload["request_id"],
        command_hash=digest,
        source=payload["source"],
        source_input=payload["source_input"],
        revision_before=payload["revision"],
        revision_after=payload["revision"] + 1,
        **extra,
    )
    for line in lines:
        item, _ = PantryItem.objects.get_or_create(
            owner=owner, ingredient_id=line["ingredient"]
        )
        item.quantity = line["after"]
        item.version = line["version"] + 1
        item.last_confirmed = timezone.now()
        item.full_clean()
        item.save()
        InventoryEventLine.objects.create(
            event=event,
            ingredient_id=line["ingredient"],
            ingredient_snapshot={
                "name": line["name"],
                "unit": line["unit"],
                "dimension": line["dimension"],
            },
            operations=line["operations"],
            before=line["before"],
            after=line["after"],
            version_before=line["version"],
            version_after=item.version,
            delta=(
                line["after"] - line["before"]
                if line["before"] is not None and line["after"] is not None
                else None
            ),
        )
    if payload["context"].get("kind") == "import":
        from pantry.services import imports

        imports.finish(owner, payload["context"], event, payload["operations"])
    return event


def _context_fields(owner, context, operations):
    if context.get("kind") == "import":
        from pantry.services import imports

        imports.check_context(owner, context, operations)
        return {}
    from pantry.services.cooking import context_fields

    return context_fields(owner, context)
