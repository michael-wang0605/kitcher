from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from pantry.models import Recipe
from pantry.recipe_forms import RecipeForm, RecipeLines
from pantry.services import recipes, shopping, suggestions
from pantry.services.quantities import Conflict, DomainError, display, servings

EDIT_SALT = "kitcher.recipe-edit.v1"
SELECTION_SALT = "kitcher.selection.v1"


def _signed(owner, value, salt):
    return signing.dumps({"owner": owner.pk, **value}, salt=salt)


def _read(owner, token, salt):
    try:
        payload = signing.loads(token, salt=salt, max_age=86400)
    except signing.BadSignature as exc:
        raise DomainError("This form expired. Reload and try again.") from exc
    if payload["owner"] != owner.pk:
        raise DomainError("This form belongs to a different account.")
    return payload


@login_required
def recipe_book(request):
    rows = []
    for recipe in Recipe.objects.filter(
        owner=request.user, archived=False
    ).prefetch_related("ingredients__ingredient"):
        try:
            recipes.requirements(request.user, recipe)
            complete = True
        except DomainError:
            complete = False
        rows.append({"recipe": recipe, "complete": complete})
    return render(request, "pantry/recipes.html", {"rows": rows})


@login_required
def edit_recipe(request, pk=None):
    recipe = (
        get_object_or_404(Recipe, pk=pk, owner=request.user, archived=False)
        if pk is not None
        else None
    )
    initial = (
        {
            "title": recipe.title,
            "default_servings": display(recipe.default_servings),
            "favorite": recipe.favorite,
            "instructions": recipe.instructions,
        }
        if recipe
        else None
    )
    line_initial = (
        [
            {
                "ingredient": line.ingredient_id,
                "amount": line.original_input.get("amount", ""),
                "unit": line.original_input.get("unit", line.ingredient.unit),
            }
            for line in recipe.ingredients.select_related("ingredient")
        ]
        if recipe
        else []
    )
    edit_token = _signed(
        request.user,
        {"recipe": pk, "revision": recipe.revision if recipe else None},
        EDIT_SALT,
    )
    form = RecipeForm(request.POST or None, initial=initial)
    lines = RecipeLines(
        request.POST or None,
        initial=line_initial,
        prefix="ingredients",
        form_kwargs={"owner": request.user},
    )
    if request.method == "POST":
        form_valid, lines_valid = form.is_valid(), lines.is_valid()
        if form_valid and lines_valid:
            try:
                expected = _read(
                    request.user, request.POST.get("edit_token", ""), EDIT_SALT
                )
                if expected["recipe"] != pk:
                    raise DomainError("This form belongs to a different recipe.")
                saved = recipes.save_recipe(
                    request.user,
                    **form.cleaned_data,
                    recipe_id=pk,
                    expected_revision=expected["revision"],
                    lines=[
                        row.cleaned_data
                        for row in lines
                        if row.cleaned_data.get("ingredient")
                        and not row.cleaned_data.get("DELETE")
                    ],
                )
            except (DomainError, Conflict) as exc:
                form.add_error(None, str(exc))
                edit_token = request.POST.get("edit_token", "")
            else:
                messages.success(request, "Recipe saved.")
                return redirect("recipe_edit", pk=saved.pk)
    return render(
        request,
        "pantry/recipe_edit.html",
        {"recipe": recipe, "form": form, "lines": lines, "edit_token": edit_token},
    )


@login_required
def shopping_view(request):
    data = shopping.calculate(request.user)
    data["selection_token"] = _signed(
        request.user, {"revision": data["selection"].revision}, SELECTION_SALT
    )
    data["choices"] = []
    selected_ids = {row["entry"].recipe_id for row in data["selected"]}
    for row in suggestions.ranked(request.user):
        if row["recipe"].pk not in selected_ids:
            data["choices"].append(row)
    return render(request, "pantry/shopping.html", data)


@login_required
@require_POST
def edit_selection(request):
    try:
        payload = _read(
            request.user, request.POST.get("selection_token", ""), SELECTION_SALT
        )
        chosen = suggestions.selection(request.user)
        entries = [
            (entry.recipe_id, Decimal(entry.desired_servings) / 1000)
            for entry in chosen.entries.all()
        ]
        action = request.POST.get("action")
        if action == "refresh":
            suggestions.refresh(request.user, payload["revision"])
        else:
            if action == "add":
                recipe_id = int(request.POST.get("recipe", ""))
                recipe = get_object_or_404(
                    Recipe, owner=request.user, pk=recipe_id, archived=False
                )
                entries.append((recipe_id, Decimal(recipe.default_servings) / 1000))
            elif action in {"servings", "remove", "swap"}:
                recipe_id = int(request.POST.get("current", ""))
                if recipe_id not in {pk for pk, _ in entries}:
                    raise DomainError("Recipe is not in your shortlist.")
                if action == "remove":
                    entries = [(pk, count) for pk, count in entries if pk != recipe_id]
                elif action == "servings":
                    count = servings(request.POST.get("servings", ""))
                    entries = [
                        (pk, count if pk == recipe_id else old) for pk, old in entries
                    ]
                else:
                    replacement = int(request.POST.get("recipe", ""))
                    recipe = get_object_or_404(
                        Recipe, owner=request.user, pk=replacement, archived=False
                    )
                    entries = [
                        (replacement, Decimal(recipe.default_servings) / 1000)
                        if pk == recipe_id
                        else (pk, old)
                        for pk, old in entries
                    ]
            else:
                raise DomainError("Unknown shortlist action.")
            suggestions.replace_selection(request.user, entries, payload["revision"])
    except (ValueError, DomainError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Shortlist updated.")
    return redirect("shopping")
