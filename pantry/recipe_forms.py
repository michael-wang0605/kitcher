from django import forms
from django.forms import formset_factory

from pantry.forms import UNIT_CHOICES
from pantry.models import Ingredient
from pantry.services.quantities import DomainError, servings


class RecipeForm(forms.Form):
    title = forms.CharField(max_length=160)
    default_servings = forms.CharField(initial="2")
    favorite = forms.BooleanField(required=False, initial=True)
    instructions = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4})
    )

    def clean_default_servings(self):
        try:
            return servings(self.cleaned_data["default_servings"])
        except DomainError as exc:
            raise forms.ValidationError(str(exc)) from exc


class RecipeLineForm(forms.Form):
    ingredient = forms.ModelChoiceField(
        queryset=Ingredient.objects.none(), required=False
    )
    amount = forms.CharField(required=False, max_length=80)
    unit = forms.ChoiceField(choices=UNIT_CHOICES, initial="count")

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = Ingredient.objects.filter(owner=owner)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("amount") and not cleaned.get("ingredient"):
            raise forms.ValidationError("Choose an ingredient for this amount.")
        return cleaned


RecipeLines = formset_factory(
    RecipeLineForm, extra=3, can_delete=True, max_num=100, validate_max=True
)
