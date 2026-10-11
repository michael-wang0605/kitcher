"""Revision-checked recipe definitions, including explicit unresolved requirements."""

from decimal import Decimal

from django.db import transaction
from django.db.models import F

from pantry.models import Ingredient, Recipe, RecipeIngredient
from pantry.services.quantities import (
    UNITS,
    Conflict,
    DomainError,
    quantity,
    scale,
    servings,
)


def servings_storage(value):
    return int(servings(value) * 1000)


def save_recipe(
    owner,
    *,
    title,
    default_servings,
    instructions="",
    favorite=True,
    lines=(),
    recipe_id=None,
    expected_revision=None,
):
    if not title.strip() or len(title.strip()) > 160:
        raise DomainError("Enter a recipe title of at most 160 characters.")
    prepared = []
    for line in lines:
        ingredient = line["ingredient"]
        if not isinstance(ingredient, Ingredient):
            ingredient = Ingredient.objects.filter(owner=owner, pk=ingredient).first()
        if ingredient is None or ingredient.owner_id != owner.pk:
            raise DomainError("Recipe ingredient belongs to a different account.")
        raw, unit = str(line.get("amount", "")), line.get("unit", ingredient.unit)
        if not raw.strip():
            amount, unresolved = None, "Enter an ingredient amount."
        elif unit not in UNITS or UNITS[unit][0] != ingredient.dimension:
            amount, unresolved = None, "Convert this amount to a compatible unit."
        else:
            amount = quantity(raw, unit, ingredient.dimension, allow_zero=False)
            unresolved = ""
        prepared.append((ingredient, amount, unresolved, {"amount": raw, "unit": unit}))
    stored_servings = servings_storage(default_servings)
    with transaction.atomic():
        if recipe_id is None:
            recipe = Recipe(owner=owner, revision=1)
        else:
            recipe = (
                Recipe.objects.select_for_update()
                .filter(owner=owner, pk=recipe_id)
                .first()
            )
            if recipe is None:
                raise DomainError("Recipe not found.")
            if recipe.revision != expected_revision:
                raise Conflict(
                    "Recipe changed since you opened it. Reload before saving."
                )
            claimed = Recipe.objects.filter(
                pk=recipe.pk, owner=owner, revision=expected_revision
            ).update(revision=F("revision") + 1)
            if claimed != 1:
                raise Conflict("Recipe changed since you opened it.")
            recipe.revision += 1
        recipe.title = title.strip()
        recipe.default_servings = stored_servings
        recipe.instructions = instructions
        recipe.favorite = favorite
        recipe.full_clean()
        recipe.save()
        recipe.ingredients.all().delete()
        for position, (ingredient, amount, unresolved, original) in enumerate(prepared):
            RecipeIngredient.objects.create(
                recipe=recipe,
                ingredient=ingredient,
                amount=amount,
                position=position,
                unresolved=unresolved,
                original_input=original,
            )
    return recipe


def requirements(owner, recipe, desired=None):
    if recipe.owner_id != owner.pk or recipe.archived:
        raise DomainError("Recipe not found.")
    lines = list(recipe.ingredients.all())
    if not lines or any(line.amount is None or line.unresolved for line in lines):
        raise DomainError("This recipe has incomplete ingredient quantities.")
    desired = (
        Decimal(recipe.default_servings) / 1000
        if desired is None
        else servings(desired)
    )
    totals, ingredients = {}, {}
    for line in lines:
        if line.ingredient.owner_id != owner.pk:
            raise DomainError("Recipe ingredient belongs to a different account.")
        totals[line.ingredient_id] = totals.get(line.ingredient_id, 0) + line.amount
        ingredients[line.ingredient_id] = line.ingredient
    return {
        pk: {
            "ingredient": ingredients[pk],
            "amount": scale(amount, desired, Decimal(recipe.default_servings) / 1000),
        }
        for pk, amount in totals.items()
    }


def snapshot(owner, recipe, desired, actual):
    needed = requirements(owner, recipe, desired)
    return {
        "id": recipe.pk,
        "revision": recipe.revision,
        "title": recipe.title,
        "default_servings": str(Decimal(recipe.default_servings) / 1000),
        "chosen_servings": str(servings(desired)),
        "instructions": recipe.instructions,
        "ingredients": [
            {
                "ingredient": pk,
                "name": value["ingredient"].name,
                "unit": value["ingredient"].unit,
                "required": value["amount"],
                "actual": actual.get(pk, value["amount"]),
            }
            for pk, value in needed.items()
        ],
    }
