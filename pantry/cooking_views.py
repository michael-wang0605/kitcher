from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from pantry.models import InventoryEvent, PantryItem, Recipe
from pantry.recipe_views import EDIT_SALT, _read
from pantry.services import cooking, recipes
from pantry.services.quantities import DomainError, display, quantity, servings


@login_required
def cook_recipe(request, pk):
    recipe = get_object_or_404(Recipe, owner=request.user, pk=pk, archived=False)
    error = None
    try:
        desired = servings(
            request.POST.get(
                "servings",
                request.GET.get(
                    "servings", str(Decimal(recipe.default_servings) / 1000)
                ),
            )
        )
        needed = recipes.requirements(request.user, recipe, desired)
    except DomainError as exc:
        return render(
            request,
            "pantry/cook.html",
            {"recipe": recipe, "error": str(exc)},
            status=422,
        )
    stock = {
        item.ingredient_id: item.quantity
        for item in PantryItem.objects.filter(owner=request.user)
    }
    rows = [
        {
            "ingredient": value["ingredient"],
            "amount": display(value["amount"]),
            "available": display(stock.get(pk)),
            "actual_input": request.POST.get(f"actual-{pk}", display(value["amount"])),
            "reconcile_input": request.POST.get(f"reconcile-{pk}", ""),
        }
        for pk, value in needed.items()
    ]
    if request.method == "POST" and request.POST.get("action") == "preview":
        try:
            actual, reconciliations = {}, {}
            for ingredient_id, value in needed.items():
                ingredient = value["ingredient"]
                actual[ingredient_id] = quantity(
                    request.POST.get(f"actual-{ingredient_id}", ""),
                    ingredient.unit,
                    ingredient.dimension,
                )
                raw = request.POST.get(f"reconcile-{ingredient_id}", "")
                if raw.strip():
                    reconciliations[ingredient_id] = quantity(
                        raw, ingredient.unit, ingredient.dimension
                    )
            reviewed = cooking.preview(
                request.user,
                recipe,
                desired,
                actual=actual,
                reconciliations=reconciliations,
                note=request.POST.get("note", "")[:1000],
            )
        except DomainError as exc:
            error = str(exc)
        else:
            return render(request, "pantry/preview.html", {"preview": reviewed})
    return render(
        request,
        "pantry/cook.html",
        {"recipe": recipe, "rows": rows, "servings": str(desired), "error": error},
    )


@login_required
@require_POST
def reverse_event(request, pk):
    event = get_object_or_404(InventoryEvent, owner=request.user, pk=pk)
    try:
        reviewed = cooking.reversal_preview(request.user, event)
    except DomainError as exc:
        messages.error(request, str(exc))
        return redirect("correction", pk=pk)
    return render(request, "pantry/preview.html", {"preview": reviewed})


@login_required
def correct_event(request, pk):
    event = get_object_or_404(InventoryEvent, owner=request.user, pk=pk)
    current = cooking.current_amounts(request.user, event)
    rows = [
        {
            "ingredient": line.ingredient,
            "current": display(current[line.ingredient_id]),
            "value": request.POST.get(
                f"amount-{line.ingredient_id}",
                ""
                if current[line.ingredient_id] is None
                else display(current[line.ingredient_id]),
            ),
        }
        for line in event.lines.select_related("ingredient")
    ]
    error = None
    if request.method == "POST":
        try:
            quantities = {}
            for row in rows:
                ingredient, raw = row["ingredient"], row["value"]
                quantities[ingredient.pk] = (
                    quantity(raw, ingredient.unit, ingredient.dimension)
                    if raw.strip()
                    else None
                )
            reviewed = cooking.correction_preview(
                request.user, event, quantities, request.POST.get("note", "")[:1000]
            )
        except DomainError as exc:
            error = str(exc)
        else:
            return render(request, "pantry/preview.html", {"preview": reviewed})
    return render(
        request,
        "pantry/correction.html",
        {
            "event": event,
            "rows": rows,
            "error": error,
            "note": request.POST.get("note", ""),
        },
    )


@login_required
@require_POST
def archive_recipe(request, pk):
    try:
        expected = _read(request.user, request.POST.get("edit_token", ""), EDIT_SALT)
        if expected["recipe"] != pk:
            raise DomainError("This form belongs to a different recipe.")
        cooking.archive_recipe(request.user, pk, expected["revision"])
    except DomainError as exc:
        messages.error(request, str(exc))
        return redirect("recipe_edit", pk=pk)
    messages.success(request, "Recipe archived. Cooking history is preserved.")
    return redirect("recipes")
