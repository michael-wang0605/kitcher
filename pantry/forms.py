from django import forms

from pantry.models import Ingredient
from pantry.services.inventory import operation
from pantry.services.quantities import UNITS, DomainError, quantity

UNIT_CHOICES = [(unit, unit.replace("us_", "US ")) for unit in UNITS]


class IngredientForm(forms.Form):
    name = forms.CharField(max_length=120)
    dimension = forms.ChoiceField(
        choices=[
            ("count", "Count (eggs, apples)"),
            ("mass", "Mass (rice, flour)"),
            ("volume", "Volume (milk, oil)"),
        ]
    )
    aliases = forms.CharField(
        required=False, help_text="Other names, separated by commas."
    )


class UpdateForm(forms.Form):
    ingredient = forms.ModelChoiceField(queryset=Ingredient.objects.none())
    kind = forms.ChoiceField(
        label="What changed?",
        choices=[
            ("set", "I have this much now"),
            ("add", "I bought / added this much"),
            ("subtract", "I used this much"),
            ("unknown", "Mark the amount unknown"),
        ],
    )
    amount = forms.CharField(required=False, help_text="Decimal amounts are welcome.")
    unit = forms.ChoiceField(choices=UNIT_CHOICES)
    reconcile = forms.CharField(
        required=False,
        label="Current amount before adding or using",
        help_text="Required for unknown stock. Also use this to reconcile tracked stock.",
    )
    note = forms.CharField(
        required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 2})
    )

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = Ingredient.objects.filter(owner=owner)

    def clean(self):
        cleaned = super().clean()
        if not all(key in cleaned for key in ("ingredient", "kind", "unit")):
            return cleaned
        ingredient, kind = cleaned["ingredient"], cleaned["kind"]
        try:
            amount = (
                None
                if kind == "unknown"
                else quantity(
                    cleaned.get("amount", ""),
                    cleaned["unit"],
                    ingredient.dimension,
                    allow_zero=kind == "set",
                )
            )
            reconcile = (
                quantity(cleaned["reconcile"], cleaned["unit"], ingredient.dimension)
                if cleaned.get("reconcile")
                else None
            )
            if kind == "unknown" and (cleaned.get("amount") or reconcile is not None):
                raise DomainError(
                    "Leave amount fields empty when marking stock unknown."
                )
            cleaned["operation"] = operation(
                ingredient,
                "set" if kind == "unknown" else kind,
                amount,
                reconcile=reconcile,
            )
        except DomainError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return cleaned


class PackageForm(forms.Form):
    ingredient = forms.ModelChoiceField(queryset=Ingredient.objects.none())
    label = forms.CharField(max_length=120, help_text="For example: 1 kg rice bag.")
    amount = forms.CharField()
    unit = forms.ChoiceField(choices=UNIT_CHOICES)

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = Ingredient.objects.filter(owner=owner)
