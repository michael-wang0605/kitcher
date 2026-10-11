from django import forms
from django.forms import formset_factory

from pantry.models import Ingredient
from pantry.services.quantities import UNITS


class TextImportForm(forms.Form):
    text = forms.CharField(
        max_length=10000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Separate statements with a new line or semicolon: bought six eggs; used 200 g rice; have three eggs left.",
    )


class PhotoImportForm(forms.Form):
    source_type = forms.ChoiceField(
        choices=[("fridge", "Fridge photo"), ("receipt", "Receipt")]
    )
    photo = forms.FileField(
        help_text="One JPEG or PNG, at most 10 MiB and 25 million pixels."
    )


class CandidateForm(forms.Form):
    ingredient = forms.ModelChoiceField(
        queryset=Ingredient.objects.none(), required=False
    )
    kind = forms.ChoiceField(
        choices=[
            ("", "Choose operation"),
            ("set", "Set observed amount"),
            ("add", "Add purchase"),
            ("subtract", "Subtract usage"),
        ],
        required=False,
    )
    amount = forms.CharField(max_length=128, required=False)
    unit = forms.ChoiceField(
        choices=[("", "Choose unit"), *[(u, u) for u in UNITS]], required=False
    )
    reconcile = forms.CharField(
        max_length=128,
        required=False,
        label="Stock before change (canonical g, ml, or count)",
        help_text="Required for ADD/SUBTRACT when current stock is unknown. Enter 0 only if confirmed.",
    )
    evidence = forms.CharField(
        max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    remove = forms.BooleanField(required=False, label="Remove this observation")

    def __init__(self, *args, owner, source_type, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ingredient"].queryset = Ingredient.objects.filter(owner=owner)
        if source_type in {"fridge", "receipt"}:
            self.fields["kind"].disabled = True
            self.fields["kind"].initial = "set" if source_type == "fridge" else "add"


CandidateFormSet = formset_factory(
    CandidateForm, extra=3, max_num=200, absolute_max=200, validate_max=True
)
