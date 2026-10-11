"""Catalog changes preserve owner-scoped canonical aliases."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from pantry.models import Ingredient, IngredientAlias, PackageSize, normalize_name
from pantry.services.quantities import DomainError, quantity


def add_alias(owner, ingredient, name):
    if ingredient.owner_id != owner.pk:
        raise DomainError("Ingredient belongs to a different account.")
    normalized = normalize_name(name)
    existing = IngredientAlias.objects.filter(
        owner=owner, normalized_name=normalized
    ).first()
    if existing:
        if existing.ingredient_id != ingredient.pk:
            raise DomainError("That name already matches another ingredient.")
        return existing
    alias = IngredientAlias(
        owner=owner,
        ingredient=ingredient,
        name=name.strip(),
        normalized_name=normalized,
    )
    alias.full_clean()
    alias.save()
    return alias


def save_ingredient(owner, *, name, dimension, aliases=(), ingredient=None):
    try:
        with transaction.atomic():
            if ingredient is None:
                ingredient = Ingredient(owner=owner)
            elif ingredient.owner_id != owner.pk:
                raise DomainError("Ingredient belongs to a different account.")
            ingredient.name = name.strip()
            ingredient.normalized_name = normalize_name(name)
            ingredient.dimension = dimension
            ingredient.full_clean()
            ingredient.save()
            for alias in [name, *aliases]:
                if alias.strip():
                    add_alias(owner, ingredient, alias)
            return ingredient
    except (ValidationError, IntegrityError) as exc:
        raise DomainError(
            "Ingredient or alias conflicts with the existing catalog."
        ) from exc


def add_package(owner, ingredient, label, amount, unit):
    if ingredient.owner_id != owner.pk:
        raise DomainError("Ingredient belongs to a different account.")
    package = PackageSize(
        owner=owner,
        ingredient=ingredient,
        label=label.strip(),
        amount=quantity(amount, unit, ingredient.dimension, allow_zero=False),
    )
    try:
        package.full_clean()
        package.save()
    except (ValidationError, IntegrityError) as exc:
        raise DomainError(
            "That package label already exists or its amount is invalid."
        ) from exc
    return package
