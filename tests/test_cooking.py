from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from pantry.models import InventoryEvent, PantryItem, Recipe
from pantry.services import catalog, cooking, inventory, recipes
from pantry.services.quantities import Conflict, DomainError


class CookingCorrectionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("cook-owner")
        self.other = get_user_model().objects.create_user("cook-other")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")
        self.recipe = recipes.save_recipe(
            self.owner,
            title="Scrambled eggs",
            default_servings="1",
            instructions="Original instructions.",
            lines=[{"ingredient": self.eggs, "amount": "2", "unit": "count"}],
        )

    def apply(self, kind, amount, reconcile=None):
        return inventory.confirm(
            self.owner,
            inventory.preview(
                self.owner,
                [inventory.operation(self.eggs, kind, amount, reconcile=reconcile)],
            )["token"],
        )

    def test_cooking_scales_adjusts_actual_usage_and_preserves_snapshot(self):
        self.apply("set", 10000)
        reviewed = cooking.preview(
            self.owner, self.recipe, "2", actual={self.eggs.pk: 3000}
        )
        event = inventory.confirm(self.owner, reviewed["token"])
        repeated = inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(repeated.pk, event.pk)
        self.assertEqual(PantryItem.objects.get().quantity, 7000)
        self.assertEqual(event.recipe_snapshot["chosen_servings"], "2")
        self.assertEqual(event.recipe_snapshot["ingredients"][0]["required"], 4000)
        self.assertEqual(event.recipe_snapshot["ingredients"][0]["actual"], 3000)
        updated = recipes.save_recipe(
            self.owner,
            recipe_id=self.recipe.pk,
            expected_revision=self.recipe.revision,
            title="Renamed",
            default_servings="2",
            instructions="Edited later.",
            lines=[{"ingredient": self.eggs, "amount": "5", "unit": "count"}],
        )
        cooking.archive_recipe(self.owner, updated.pk, updated.revision)
        event.refresh_from_db()
        self.assertEqual(event.recipe_snapshot["title"], "Scrambled eggs")
        self.assertEqual(
            event.recipe_snapshot["instructions"], "Original instructions."
        )
        self.assertTrue(Recipe.objects.get().archived)

    def test_stale_recipe_revision_rolls_back_then_refreshes_requirement(self):
        self.apply("set", 10000)
        reviewed = cooking.preview(self.owner, self.recipe, "1")
        recipes.save_recipe(
            self.owner,
            recipe_id=self.recipe.pk,
            expected_revision=self.recipe.revision,
            title="Larger meal",
            default_servings="1",
            lines=[{"ingredient": self.eggs, "amount": "3", "unit": "count"}],
        )
        with self.assertRaises(Conflict):
            inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 10000)
        refreshed = inventory.refresh(self.owner, reviewed["token"])
        self.assertEqual(refreshed["payload"]["operations"][0]["amount"], 3000)
        event = inventory.confirm(self.owner, refreshed["token"])
        self.assertEqual(event.recipe_snapshot["revision"], 2)
        self.assertEqual(PantryItem.objects.get().quantity, 7000)

    def test_unknown_and_insufficient_stock_require_reconciliation(self):
        with self.assertRaises(DomainError):
            cooking.preview(self.owner, self.recipe, "1")
        reviewed = cooking.preview(
            self.owner, self.recipe, "1", reconciliations={self.eggs.pk: 6000}
        )
        event = inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 4000)
        self.assertEqual(event.lines.get().operations[0]["reconcile"], 6000)
        with self.assertRaises(DomainError):
            cooking.preview(self.owner, self.recipe, "3")

    def test_actual_zero_is_recorded_without_inventing_unknown_stock(self):
        event = inventory.confirm(
            self.owner,
            cooking.preview(self.owner, self.recipe, "1", actual={self.eggs.pk: 0})[
                "token"
            ],
        )
        self.assertIsNone(PantryItem.objects.get().quantity)
        self.assertEqual(event.recipe_snapshot["ingredients"][0]["actual"], 0)

    def test_reversal_uses_current_stock_and_cannot_happen_twice(self):
        self.apply("set", 4000)
        purchase = self.apply("add", 6000)
        self.apply("subtract", 2000)
        reviewed = cooking.reversal_preview(self.owner, purchase)
        reversal = inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 2000)
        self.assertEqual(reversal.reversal_of_id, purchase.pk)
        self.assertEqual(
            inventory.confirm(self.owner, reviewed["token"]).pk, reversal.pk
        )
        with self.assertRaises(DomainError):
            cooking.reversal_preview(self.owner, purchase)

    def test_negative_or_unknown_reversal_requires_linked_correction(self):
        self.apply("set", 0)
        purchase = self.apply("add", 6000)
        self.apply("subtract", 2000)
        with self.assertRaises(DomainError):
            cooking.reversal_preview(self.owner, purchase)
        correction = inventory.confirm(
            self.owner,
            cooking.correction_preview(
                self.owner, purchase, {self.eggs.pk: 1000}, "Checked remaining eggs"
            )["token"],
        )
        self.assertEqual(correction.correction_of_id, purchase.pk)
        self.assertIsNone(correction.reversal_of_id)
        self.assertEqual(PantryItem.objects.get().quantity, 1000)
        unknown_change = self.apply("set", None)
        with self.assertRaises(DomainError):
            cooking.reversal_preview(self.owner, unknown_change)

    def test_absolute_correction_reverses_its_effective_delta(self):
        self.apply("set", 4000)
        correction = self.apply("set", 6000)
        self.apply("add", 3000)
        inventory.confirm(
            self.owner, cooking.reversal_preview(self.owner, correction)["token"]
        )
        self.assertEqual(PantryItem.objects.get().quantity, 7000)

    def test_cross_owner_contexts_and_stale_reversals_are_rejected(self):
        original = self.apply("set", 4000)
        with self.assertRaises(DomainError):
            cooking.reversal_preview(self.other, original)
        with self.assertRaises(DomainError):
            cooking.correction_preview(
                self.other, original, {self.eggs.pk: 1000}, "Changed"
            )
        purchase = self.apply("add", 2000)
        reviewed = cooking.reversal_preview(self.owner, purchase)
        self.apply("add", 1000)
        with self.assertRaises(Conflict):
            inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(InventoryEvent.objects.filter(reversal_of=purchase).count(), 0)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CookingInterfaceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("cook-ui")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")
        self.recipe = recipes.save_recipe(
            self.owner,
            title="Egg lunch",
            default_servings="1",
            lines=[{"ingredient": self.eggs, "amount": "2", "unit": "count"}],
        )
        self.client.force_login(self.owner)

    def test_cook_review_confirm_history_and_correction_forms(self):
        self.assertEqual(
            self.client.get(reverse("cook", args=[self.recipe.pk])).status_code, 200
        )
        page = self.client.post(
            reverse("cook", args=[self.recipe.pk]),
            {
                "servings": "1",
                "action": "preview",
                f"actual-{self.eggs.pk}": "2",
                f"reconcile-{self.eggs.pk}": "6",
                "note": "Lunch",
            },
        )
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("confirm"), {"token": page.context["preview"]["token"]}
        )
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response.url), "Egg lunch")
        event = InventoryEvent.objects.get(source="cooking")
        correction = self.client.post(
            reverse("correction", args=[event.pk]),
            {f"amount-{self.eggs.pk}": "3", "note": "Counted again"},
        )
        self.assertEqual(correction.status_code, 200)
        self.client.post(
            reverse("confirm"), {"token": correction.context["preview"]["token"]}
        )
        self.assertEqual(PantryItem.objects.get().quantity, 3000)
