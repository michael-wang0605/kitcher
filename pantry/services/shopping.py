"""Selected recipes are alternatives: target their largest individual need."""

from decimal import Decimal

from pantry.models import PantryItem
from pantry.services.quantities import DomainError, display
from pantry.services.recipes import requirements
from pantry.services.suggestions import selection


def calculate(owner):
    chosen = selection(owner)
    stock = {
        item.ingredient_id: item.quantity
        for item in PantryItem.objects.filter(owner=owner)
    }
    targets, selected = {}, []
    for entry in chosen.entries.select_related("recipe").prefetch_related(
        "recipe__ingredients__ingredient"
    ):
        recipe = entry.recipe
        desired = Decimal(entry.desired_servings) / 1000
        try:
            needed = requirements(owner, recipe, desired)
        except DomainError as exc:
            selected.append(
                {
                    "entry": entry,
                    "reason": str(exc),
                    "ready": False,
                    "changed": entry.seen_recipe_revision != recipe.revision,
                }
            )
            continue
        shortages, unknown = [], []
        for pk, value in needed.items():
            available = stock.get(pk)
            if available is None:
                unknown.append(value["ingredient"].name)
            elif available < value["amount"]:
                shortages.append(value["ingredient"].name)
            if pk not in targets or value["amount"] > targets[pk]["target"]:
                targets[pk] = {
                    "ingredient": value["ingredient"],
                    "target": value["amount"],
                }
        selected.append(
            {
                "entry": entry,
                "ready": not shortages and not unknown,
                "changed": entry.seen_recipe_revision != recipe.revision,
                "reason": "Ready to cook"
                if not shortages and not unknown
                else "; ".join(
                    filter(
                        None,
                        [
                            "Missing " + ", ".join(shortages) if shortages else "",
                            "Check " + ", ".join(unknown) if unknown else "",
                        ],
                    )
                ),
            }
        )
    lines = []
    for pk, value in sorted(
        targets.items(), key=lambda pair: pair[1]["ingredient"].normalized_name
    ):
        available = stock.get(pk)
        buy = None if available is None else max(value["target"] - available, 0)
        ingredient = value["ingredient"]
        lines.append(
            {
                **value,
                "available": available,
                "buy": buy,
                "unknown": available is None,
                "target_display": display(value["target"]),
                "available_display": display(available),
                "buy_display": display(buy),
                "purchase_display": (
                    str((buy + 999) // 1000)
                    if buy is not None and ingredient.dimension == "count"
                    else display(buy)
                ),
            }
        )
    return {"selection": chosen, "selected": selected, "lines": lines}
