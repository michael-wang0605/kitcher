"""Deterministic favorites and explicit, revision-checked shortlist edits."""

from decimal import Decimal

from django.db import transaction
from django.db.models import F

from pantry.models import PantryItem, Recipe, RecipeSelection, SelectionEntry
from pantry.services.quantities import Conflict, DomainError
from pantry.services.recipes import requirements, servings_storage


def ranked(owner):
    stock = {
        item.ingredient_id: item.quantity
        for item in PantryItem.objects.filter(owner=owner)
    }
    result = []
    for recipe in Recipe.objects.filter(
        owner=owner, favorite=True, archived=False
    ).prefetch_related("ingredients__ingredient"):
        try:
            needed = requirements(owner, recipe)
        except DomainError:
            continue
        shortages, unknown, coverage = [], [], []
        for pk, value in needed.items():
            available = stock.get(pk)
            if available is None:
                unknown.append(value["ingredient"].name)
                coverage.append(Decimal(0))
            else:
                if available < value["amount"]:
                    shortages.append(value["ingredient"].name)
                coverage.append(min(Decimal(available) / value["amount"], Decimal(1)))
        average = sum(coverage) / len(coverage)
        ready = not shortages and not unknown
        reason = (
            "Ready to cook"
            if ready
            else "; ".join(
                filter(
                    None,
                    [
                        "Missing " + ", ".join(shortages) if shortages else "",
                        "Check " + ", ".join(unknown) if unknown else "",
                    ],
                )
            )
        )
        result.append(
            {
                "recipe": recipe,
                "ready": ready,
                "reason": reason,
                "sort": (not ready, len(shortages) + len(unknown), -average, recipe.pk),
            }
        )
    return sorted(result, key=lambda row: row["sort"])


def selection(owner):
    return RecipeSelection.objects.get_or_create(owner=owner)[0]


def replace_selection(owner, entries, expected_revision):
    if len(entries) != len({recipe_id for recipe_id, _ in entries}):
        raise DomainError("A recipe can appear only once in the shortlist.")
    prepared = []
    for recipe_id, desired in entries:
        recipe = Recipe.objects.filter(
            owner=owner, pk=recipe_id, archived=False
        ).first()
        if recipe is None:
            raise DomainError("Recipe not found.")
        requirements(owner, recipe, desired)
        prepared.append((recipe, servings_storage(desired)))
    with transaction.atomic():
        chosen = selection(owner)
        changed = RecipeSelection.objects.filter(
            pk=chosen.pk, revision=expected_revision
        ).update(revision=F("revision") + 1)
        if changed != 1:
            raise Conflict("Shortlist changed. Reload before editing it.")
        chosen.entries.all().delete()
        for position, (recipe, desired) in enumerate(prepared):
            SelectionEntry.objects.create(
                selection=chosen,
                recipe=recipe,
                desired_servings=desired,
                position=position,
                seen_recipe_revision=recipe.revision,
            )
    chosen.refresh_from_db()
    return chosen


def refresh(owner, expected_revision):
    entries = [
        (row["recipe"].pk, Decimal(row["recipe"].default_servings) / 1000)
        for row in ranked(owner)[:3]
    ]
    return replace_selection(owner, entries, expected_revision)
