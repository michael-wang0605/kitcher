from django import forms
from django.contrib import admin
from django.core import signing
from django.core.exceptions import ValidationError

from pantry.models import (
    Ingredient,
    IngredientAlias,
    InventoryEvent,
    InventoryEventLine,
    PackageSize,
    PantryItem,
    PantryState,
    Recipe,
    RecipeIngredient,
)
from pantry.services import catalog, recipes
from pantry.services.quantities import DomainError, display, servings


class OwnedAdmin(admin.ModelAdmin):
    exclude = ["owner"]

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class OwnedForm(base):
            def __init__(self, *args, **options):
                super().__init__(*args, **options)
                if hasattr(self.instance, "owner_id"):
                    self.instance.owner = request.user

        return OwnedForm

    def get_queryset(self, request):
        return super().get_queryset(request).filter(owner=request.user)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        model = db_field.remote_field.model
        if any(field.name == "owner" for field in model._meta.fields):
            kwargs["queryset"] = model.objects.filter(owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        obj.owner = request.user
        obj.full_clean()
        obj.save()


@admin.register(Ingredient)
class IngredientAdmin(OwnedAdmin):
    list_display = ["name", "dimension"]
    readonly_fields = ["normalized_name"]

    def save_model(self, request, obj, form, change):
        saved = catalog.save_ingredient(
            request.user,
            name=obj.name,
            dimension=obj.dimension,
            ingredient=obj if change else None,
        )
        obj.pk = saved.pk
        obj.owner = saved.owner
        obj.normalized_name = saved.normalized_name
        obj._state = saved._state


@admin.register(IngredientAlias)
class AliasAdmin(OwnedAdmin):
    readonly_fields = ["normalized_name"]

    def save_model(self, request, obj, form, change):
        # Preserve alias identity once created; add new aliases through the same catalog.
        if change:
            return
        saved = catalog.add_alias(request.user, obj.ingredient, obj.name)
        obj.pk = saved.pk
        obj.owner = saved.owner
        obj.normalized_name = saved.normalized_name
        obj._state = saved._state

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PackageSize, OwnedAdmin)


class ReadOnlyAdmin(OwnedAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PantryItem, ReadOnlyAdmin)
admin.site.register(PantryState, ReadOnlyAdmin)
admin.site.register(InventoryEvent, ReadOnlyAdmin)


@admin.register(InventoryEventLine)
class EventLineAdmin(ReadOnlyAdmin):
    def get_queryset(self, request):
        return admin.ModelAdmin.get_queryset(self, request).filter(
            event__owner=request.user
        )


# Recipe edits share the service/revision contract used by the normal interface.

ADMIN_RECIPE_SALT = "kitcher.admin-recipe.v1"


class RecipeAdminForm(forms.ModelForm):
    servings = forms.CharField(label="Default servings", initial="2")
    revision_token = forms.CharField(widget=forms.HiddenInput)

    class Meta:
        model = Recipe
        fields = ["title", "favorite", "instructions"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["servings"].initial = display(self.instance.default_servings)

    def clean(self):
        cleaned = super().clean()
        try:
            cleaned["servings"] = servings(cleaned.get("servings", ""))
            payload = signing.loads(
                cleaned.get("revision_token", ""), salt=ADMIN_RECIPE_SALT, max_age=86400
            )
            if (
                payload["owner"] != self.instance.owner_id
                or payload["recipe"] != self.instance.pk
            ):
                raise DomainError("This form belongs to a different recipe.")
            if self.instance.pk and payload["revision"] != self.instance.revision:
                raise DomainError("Recipe changed. Reload before saving.")
            cleaned["expected_revision"] = payload["revision"]
        except (DomainError, signing.BadSignature, KeyError) as exc:
            raise ValidationError(str(exc)) from exc
        return cleaned


@admin.register(Recipe)
class RecipeAdmin(OwnedAdmin):
    form = RecipeAdminForm
    fields = ["title", "favorite", "servings", "instructions", "revision_token"]
    list_display = ["title", "favorite", "revision"]

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class BoundRecipeForm(base):
            def __init__(self, *args, **options):
                super().__init__(*args, **options)
                self.fields["revision_token"].initial = signing.dumps(
                    {
                        "owner": request.user.pk,
                        "recipe": obj.pk if obj else None,
                        "revision": obj.revision if obj else None,
                    },
                    salt=ADMIN_RECIPE_SALT,
                )

        return BoundRecipeForm

    def save_model(self, request, obj, form, change):
        lines = (
            [
                {
                    "ingredient": line.ingredient,
                    "amount": line.original_input.get("amount", display(line.amount)),
                    "unit": line.original_input.get("unit", line.ingredient.unit),
                }
                for line in obj.ingredients.select_related("ingredient")
            ]
            if change
            else []
        )
        saved = recipes.save_recipe(
            request.user,
            title=obj.title,
            default_servings=form.cleaned_data["servings"],
            instructions=obj.instructions,
            favorite=obj.favorite,
            lines=lines,
            recipe_id=obj.pk if change else None,
            expected_revision=form.cleaned_data["expected_revision"],
        )
        obj.pk, obj.owner, obj.revision = saved.pk, saved.owner, saved.revision
        obj.default_servings, obj._state = saved.default_servings, saved._state

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecipeIngredient)
class RecipeIngredientAdmin(ReadOnlyAdmin):
    def get_queryset(self, request):
        return admin.ModelAdmin.get_queryset(self, request).filter(
            recipe__owner=request.user
        )
