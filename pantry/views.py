from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from pantry.forms import IngredientForm, PackageForm, UpdateForm
from pantry.models import Ingredient, InventoryEvent, PantryItem
from pantry.services import catalog, inventory
from pantry.services.quantities import Busy, Conflict, DomainError, display


@login_required
def pantry(request):
    items = {
        item.ingredient_id: item
        for item in PantryItem.objects.filter(owner=request.user)
    }
    rows = []
    for ingredient in Ingredient.objects.filter(owner=request.user):
        item = items.get(ingredient.pk)
        value = item.quantity if item else None
        rows.append(
            {
                "ingredient": ingredient,
                "quantity": display(value),
                "known": value is not None,
                "confirmed": item.last_confirmed if item else None,
            }
        )
    form = UpdateForm(owner=request.user)
    return render(request, "pantry/pantry.html", {"rows": rows, "form": form})


@login_required
def ingredients(request):
    form = IngredientForm()
    package_form = PackageForm(owner=request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "ingredient":
            form = IngredientForm(request.POST)
            if form.is_valid():
                try:
                    catalog.save_ingredient(
                        request.user,
                        name=form.cleaned_data["name"],
                        dimension=form.cleaned_data["dimension"],
                        aliases=form.cleaned_data["aliases"].split(","),
                    )
                except DomainError as exc:
                    form.add_error(None, str(exc))
                else:
                    messages.success(
                        request,
                        "Ingredient added. Its stock is unknown until confirmed.",
                    )
                    return redirect("ingredients")
        elif action == "package":
            package_form = PackageForm(request.POST, owner=request.user)
            if package_form.is_valid():
                try:
                    catalog.add_package(request.user, **package_form.cleaned_data)
                except DomainError as exc:
                    package_form.add_error(None, str(exc))
                else:
                    messages.success(request, "Package size saved.")
                    return redirect("ingredients")
    return render(
        request,
        "pantry/ingredients.html",
        {
            "ingredients": Ingredient.objects.filter(
                owner=request.user
            ).prefetch_related("aliases", "packages"),
            "form": form,
            "package_form": package_form,
        },
    )


@login_required
@require_POST
def preview_update(request):
    form = UpdateForm(request.POST, owner=request.user)
    if form.is_valid():
        try:
            reviewed = inventory.preview(
                request.user,
                [form.cleaned_data["operation"]],
                source_input={
                    "note": form.cleaned_data["note"],
                    "amount": form.cleaned_data["amount"],
                    "unit": form.cleaned_data["unit"],
                },
            )
        except DomainError as exc:
            form.add_error(None, str(exc))
        else:
            return render(request, "pantry/preview.html", {"preview": reviewed})
    return render(request, "pantry/update.html", {"form": form}, status=422)


@login_required
@require_POST
def confirm_update(request):
    token = request.POST.get("token", "")
    try:
        event = inventory.confirm(request.user, token)
    except Busy as exc:
        payload = inventory.read_token(request.user, token)
        return render(
            request,
            "pantry/preview.html",
            {
                "preview": {
                    "token": token,
                    "payload": payload,
                    "lines": payload["review"],
                },
                "error": str(exc),
            },
            status=409,
        )
    except Conflict as exc:
        try:
            reviewed = inventory.refresh(request.user, token)
        except DomainError as invalid:
            return render(
                request,
                "pantry/update.html",
                {"form": UpdateForm(owner=request.user), "error": str(invalid)},
                status=409,
            )
        return render(
            request,
            "pantry/preview.html",
            {"preview": reviewed, "error": str(exc)},
            status=409,
        )
    except DomainError as exc:
        return render(
            request,
            "pantry/update.html",
            {"form": UpdateForm(owner=request.user), "error": str(exc)},
            status=422,
        )
    messages.success(request, "Inventory confirmed.")
    return redirect("event", pk=event.pk)


@login_required
def history(request):
    events = InventoryEvent.objects.filter(owner=request.user).prefetch_related("lines")
    return render(
        request,
        "pantry/history.html",
        {"page": Paginator(events, 20).get_page(request.GET.get("page"))},
    )


@login_required
def event_detail(request, pk):
    event = get_object_or_404(
        InventoryEvent.objects.filter(owner=request.user).prefetch_related("lines"),
        pk=pk,
    )
    return render(request, "pantry/event.html", {"event": event})
