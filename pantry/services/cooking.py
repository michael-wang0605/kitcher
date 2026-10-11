"""Cooking and reversal previews use the shared inventory write path."""

from django.shortcuts import get_object_or_404

from pantry.models import InventoryEvent, PantryItem, Recipe
from pantry.services import inventory, recipes
from pantry.services.quantities import Conflict, DomainError, validate_stored


def preview(owner, recipe, desired, *, actual=None, reconciliations=None, note=""):
    actual = actual or {}
    reconciliations = reconciliations or {}
    needed = recipes.requirements(owner, recipe, desired)
    if set(actual) - set(needed) or set(reconciliations) - set(needed):
        raise DomainError("Usage contains an ingredient outside this recipe.")
    operations = []
    for pk, value in needed.items():
        amount = validate_stored(actual.get(pk, value["amount"]))
        operations.append(
            inventory.operation(
                value["ingredient"],
                "subtract",
                amount,
                reconcile=reconciliations.get(pk),
            )
        )
    recipe_snapshot = recipes.snapshot(owner, recipe, desired, actual)
    context = {
        "kind": "cooking",
        "recipe": recipe.pk,
        "revision": recipe.revision,
        "desired": str(desired),
        "snapshot": recipe_snapshot,
    }
    return inventory.preview(
        owner,
        operations,
        source="cooking",
        source_input={"note": note},
        context=context,
    )


def refresh(owner, payload):
    recipe = get_object_or_404(Recipe, owner=owner, pk=payload["context"]["recipe"])
    old = {
        line["ingredient"]: line
        for line in payload["context"]["snapshot"]["ingredients"]
    }
    actual, reconciliations = {}, {}
    needed = recipes.requirements(owner, recipe, payload["context"]["desired"])
    for pk in needed:
        if pk in old and old[pk]["actual"] != old[pk]["required"]:
            actual[pk] = old[pk]["actual"]
    for op in payload["operations"]:
        if op["ingredient"] in needed and op.get("reconcile") is not None:
            reconciliations[op["ingredient"]] = op["reconcile"]
    return preview(
        owner,
        recipe,
        payload["context"]["desired"],
        actual=actual,
        reconciliations=reconciliations,
        note=payload["source_input"].get("note", ""),
    )


def reversal_preview(owner, event):
    if event.owner_id != owner.pk:
        raise DomainError("Update belongs to a different account.")
    if InventoryEvent.objects.filter(reversal_of=event).exists():
        raise DomainError("This update has already been reversed.")
    operations = []
    for line in event.lines.select_related("ingredient"):
        if line.delta is None:
            raise DomainError(
                "This update changed what was known. Reconcile current quantities instead."
            )
        operations.append(
            inventory.operation(
                line.ingredient,
                "subtract" if line.delta >= 0 else "add",
                abs(line.delta),
            )
        )
    return inventory.preview(
        owner,
        operations,
        source="reversal",
        context={"kind": "reversal", "event": event.pk},
        source_input={"note": f"Reversal of update {event.pk}"},
    )


def correction_preview(owner, event, quantities, note):
    if event.owner_id != owner.pk:
        raise DomainError("Update belongs to a different account.")
    if not note.strip():
        raise DomainError("Explain the correction so the history stays understandable.")
    lines = list(event.lines.select_related("ingredient"))
    expected = {line.ingredient_id for line in lines}
    if set(quantities) != expected:
        raise DomainError("Review all affected ingredient amounts.")
    operations = [
        inventory.operation(line.ingredient, "set", quantities[line.ingredient_id])
        for line in lines
    ]
    return inventory.preview(
        owner,
        operations,
        source="correction",
        context={"kind": "correction", "event": event.pk},
        source_input={"note": note},
    )


def context_fields(owner, context):
    kind = context.get("kind")
    if kind == "cooking":
        recipe = (
            Recipe.objects.select_for_update()
            .filter(owner=owner, pk=context.get("recipe"), archived=False)
            .first()
        )
        if recipe is None or recipe.revision != context.get("revision"):
            raise Conflict(
                "Recipe changed after your preview. Review the current ingredients."
            )
        return {"recipe_snapshot": context["snapshot"]}
    if kind in {"reversal", "correction"}:
        event = InventoryEvent.objects.filter(
            owner=owner, pk=context.get("event")
        ).first()
        if event is None:
            raise DomainError("Original update not found.")
        if kind == "reversal":
            if InventoryEvent.objects.filter(reversal_of=event).exists():
                raise DomainError("This update has already been reversed.")
            if any(line.delta is None for line in event.lines.all()):
                raise DomainError(
                    "Reconcile quantities instead of automatically reversing this update."
                )
            return {"reversal_of": event}
        return {"correction_of": event}
    raise DomainError("Unknown inventory preview context.")


def current_amounts(owner, event):
    if event.owner_id != owner.pk:
        raise DomainError("Update belongs to a different account.")
    stock = {
        item.ingredient_id: item.quantity
        for item in PantryItem.objects.filter(owner=owner)
    }
    return {
        line.ingredient_id: stock.get(line.ingredient_id) for line in event.lines.all()
    }


def archive_recipe(owner, recipe_id, expected_revision):
    from django.db import transaction
    from django.db.models import F

    with transaction.atomic():
        recipe = (
            Recipe.objects.select_for_update().filter(owner=owner, pk=recipe_id).first()
        )
        if recipe is None:
            raise DomainError("Recipe not found.")
        if recipe.revision != expected_revision:
            raise Conflict("Recipe changed. Reload before archiving.")
        Recipe.objects.filter(pk=recipe.pk).update(
            archived=True, revision=F("revision") + 1
        )
