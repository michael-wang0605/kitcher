"""Owned domain records; balances are written only by the inventory service."""

import unicodedata
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from pantry.services.quantities import CANONICAL, MAX_QUANTITY


def normalize_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class Owned(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        abstract = True


class Ingredient(Owned):
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=240, editable=False)
    dimension = models.CharField(
        max_length=8,
        choices=[("count", "Count"), ("mass", "Mass"), ("volume", "Volume")],
    )

    class Meta:
        ordering = ["normalized_name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_name"], name="ingredient_owner_name"
            ),
            models.CheckConstraint(
                condition=Q(dimension__in=["count", "mass", "volume"]),
                name="ingredient_dimension",
            ),
        ]

    @property
    def unit(self):
        return CANONICAL[self.dimension]

    def clean(self):
        self.normalized_name = normalize_name(self.name)
        if not self.normalized_name:
            raise ValidationError({"name": "Enter an ingredient name."})
        if self.pk:
            old = Ingredient.objects.get(pk=self.pk)
            if old.owner_id != self.owner_id:
                raise ValidationError("Ingredient ownership cannot change.")
            used = (
                self.items.exists()
                or self.event_lines.exists()
                or self.packages.exists()
            )
            if hasattr(self, "recipe_lines"):
                used = used or self.recipe_lines.exists()
            if used and old.dimension != self.dimension:
                raise ValidationError(
                    "A used ingredient's measurement dimension cannot change."
                )

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class IngredientAlias(Owned):
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="aliases"
    )
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=240, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "normalized_name"], name="alias_owner_name"
            )
        ]

    def clean(self):
        self.normalized_name = normalize_name(self.name)
        if self.ingredient_id and self.ingredient.owner_id != self.owner_id:
            raise ValidationError("Alias belongs to a different account.")
        if not self.normalized_name:
            raise ValidationError({"name": "Enter an alias."})

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PackageSize(Owned):
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="packages"
    )
    label = models.CharField(max_length=120)
    amount = models.BigIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0, amount__lte=MAX_QUANTITY),
                name="package_positive",
            ),
            models.UniqueConstraint(
                fields=["owner", "ingredient", "label"], name="package_owner_label"
            ),
        ]

    def clean(self):
        if self.ingredient_id and self.ingredient.owner_id != self.owner_id:
            raise ValidationError("Package belongs to a different account.")


class PantryState(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField(default=0)


class PantryItem(Owned):
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="items"
    )
    quantity = models.BigIntegerField(null=True, blank=True)
    version = models.PositiveBigIntegerField(default=0)
    last_confirmed = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "ingredient"], name="pantry_owner_ingredient"
            ),
            models.CheckConstraint(
                condition=Q(quantity__isnull=True)
                | Q(quantity__gte=0, quantity__lte=MAX_QUANTITY),
                name="pantry_nonnegative",
            ),
        ]

    def clean(self):
        if self.ingredient_id and self.ingredient.owner_id != self.owner_id:
            raise ValidationError("Pantry item belongs to a different account.")


class InventoryEvent(Owned):
    request_id = models.UUIDField()
    command_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)
    revision_before = models.PositiveBigIntegerField()
    revision_after = models.PositiveBigIntegerField()
    source_input = models.JSONField(default=dict)
    recipe_snapshot = models.JSONField(default=dict)
    reversal_of = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )
    correction_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="corrections",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "request_id"], name="event_owner_request"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Inventory events are immutable.")
        super().save(*args, **kwargs)


class InventoryEventLine(models.Model):
    event = models.ForeignKey(
        InventoryEvent, on_delete=models.PROTECT, related_name="lines"
    )
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="event_lines"
    )
    ingredient_snapshot = models.JSONField(default=dict)
    operations = models.JSONField()
    before = models.BigIntegerField(null=True)
    after = models.BigIntegerField(null=True)
    version_before = models.PositiveBigIntegerField()
    version_after = models.PositiveBigIntegerField()
    delta = models.BigIntegerField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["event", "ingredient"], name="event_line_ingredient"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Inventory event lines are immutable.")
        super().save(*args, **kwargs)


class Recipe(Owned):
    title = models.CharField(max_length=160)
    favorite = models.BooleanField(default=True)
    default_servings = models.PositiveIntegerField(default=1000)
    instructions = models.TextField(blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["title", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(default_servings__gte=1, default_servings__lte=1000000),
                name="recipe_positive_servings",
            ),
        ]

    def __str__(self):
        return self.title


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE, related_name="ingredients"
    )
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name="recipe_lines"
    )
    amount = models.BigIntegerField(null=True, blank=True)
    original_input = models.JSONField(default=dict)
    unresolved = models.CharField(max_length=240, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__isnull=True)
                | Q(amount__gt=0, amount__lte=MAX_QUANTITY),
                name="recipe_line_positive",
            ),
        ]


class RecipeSelection(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField(default=0)


class SelectionEntry(models.Model):
    selection = models.ForeignKey(
        RecipeSelection, on_delete=models.CASCADE, related_name="entries"
    )
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT)
    desired_servings = models.PositiveIntegerField(default=1000)
    position = models.PositiveIntegerField()
    seen_recipe_revision = models.PositiveBigIntegerField()

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["selection", "recipe"], name="selection_unique_recipe"
            ),
            models.UniqueConstraint(
                fields=["selection", "position"], name="selection_unique_position"
            ),
            models.CheckConstraint(
                condition=Q(desired_servings__gte=1, desired_servings__lte=1000000),
                name="selection_positive_servings",
            ),
        ]


class ImportDraft(Owned):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schema_version = models.PositiveIntegerField(default=1)
    source_type = models.CharField(
        max_length=8,
        choices=[("text", "Text"), ("fridge", "Fridge"), ("receipt", "Receipt")],
    )
    source_text = models.TextField(blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    duplicate_source = models.BooleanField(default=False)
    duplicate_ack = models.BooleanField(default=False)
    status = models.CharField(max_length=16, default="needs_review")
    revision = models.PositiveBigIntegerField(default=1)
    candidates = models.JSONField(default=list)
    error = models.TextField(blank=True)
    applied_event = models.OneToOneField(
        InventoryEvent,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="import_draft",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "processing",
                        "needs_review",
                        "applied",
                        "cancelled",
                        "expired",
                    ]
                ),
                name="import_draft_state",
            ),
            models.CheckConstraint(
                condition=Q(source_type__in=["text", "fridge", "receipt"]),
                name="import_source_type",
            ),
            models.CheckConstraint(
                condition=Q(status="applied", applied_event__isnull=False)
                | (~Q(status="applied") & Q(applied_event__isnull=True)),
                name="import_applied_event",
            ),
        ]


class ImportJob(Owned):
    draft = models.OneToOneField(
        ImportDraft, on_delete=models.CASCADE, related_name="job"
    )
    state = models.CharField(max_length=16, default="queued", db_index=True)
    source_name = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    attempt_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    claimed_revision = models.PositiveBigIntegerField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    state__in=[
                        "queued",
                        "processing",
                        "succeeded",
                        "failed",
                        "cancelled",
                        "expired",
                    ]
                ),
                name="import_job_state",
            ),
            models.CheckConstraint(
                condition=Q(attempts__lte=3), name="import_attempt_limit"
            ),
        ]
        indexes = [
            models.Index(fields=["state", "next_attempt_at"], name="import_ready")
        ]


class WorkerStatus(models.Model):
    name = models.CharField(primary_key=True, max_length=32)
    last_seen = models.DateTimeField()
    detail = models.CharField(max_length=240, blank=True)
