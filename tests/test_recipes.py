from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from pantry.models import InventoryEvent, PantryItem
from pantry.services import catalog, inventory, recipes, shopping, suggestions
from pantry.services.quantities import Conflict, DomainError


class RecipeShoppingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("meal-owner")
        self.other = get_user_model().objects.create_user("meal-other")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")
        self.rice = catalog.save_ingredient(self.owner, name="Rice", dimension="mass")

    def recipe(self, title, eggs, rice=None, default="2"):
        lines = [{"ingredient": self.eggs, "amount": str(eggs), "unit": "count"}]
        if rice is not None:
            lines.append({"ingredient": self.rice, "amount": str(rice), "unit": "g"})
        return recipes.save_recipe(
            self.owner, title=title, default_servings=default, lines=lines
        )

    def stock(self, ingredient, amount):
        inventory.confirm(
            self.owner,
            inventory.preview(
                self.owner, [inventory.operation(ingredient, "set", amount)]
            )["token"],
        )

    def select(self, entries):
        chosen = suggestions.selection(self.owner)
        return suggestions.replace_selection(self.owner, entries, chosen.revision)

    def test_maximum_targets_original_example_and_serving_changes(self):
        first = self.recipe("Meal A", 2, 200)
        second = self.recipe("Meal B", 3, 300)
        self.stock(self.eggs, 1000)
        self.stock(self.rice, 100000)
        self.select([(first.pk, "2"), (second.pk, "2")])
        result = {
            row["ingredient"].pk: row for row in shopping.calculate(self.owner)["lines"]
        }
        self.assertEqual(result[self.eggs.pk]["buy"], 2000)
        self.assertEqual(result[self.rice.pk]["buy"], 200000)
        self.select([(first.pk, "3")])
        result = {
            row["ingredient"].pk: row for row in shopping.calculate(self.owner)["lines"]
        }
        self.assertEqual(result[self.rice.pk]["target"], 300000)
        self.assertEqual(result[self.rice.pk]["buy"], 200000)

    def test_repeated_lines_are_summed_before_maximum(self):
        first = recipes.save_recipe(
            self.owner,
            title="Double eggs",
            default_servings="1",
            lines=[
                {"ingredient": self.eggs, "amount": "2", "unit": "count"},
                {"ingredient": self.eggs, "amount": "2", "unit": "count"},
            ],
        )
        second = self.recipe("Other eggs", 3, default="1")
        self.stock(self.eggs, 1000)
        self.select([(first.pk, "1"), (second.pk, "1")])
        self.assertEqual(shopping.calculate(self.owner)["lines"][0]["buy"], 3000)

    def test_fractional_counts_keep_exact_shortfall_and_whole_purchase_advice(self):
        meal = self.recipe("Egg meal", 3, default="2")
        self.stock(self.eggs, 1000)
        self.select([(meal.pk, "1")])
        row = shopping.calculate(self.owner)["lines"][0]
        self.assertEqual(row["buy"], 500)
        self.assertEqual(row["buy_display"], "0.5")
        self.assertEqual(row["purchase_display"], "1")

    def test_unknown_stock_is_not_absent_or_definite_purchase(self):
        meal = self.recipe("Unverified eggs", 2)
        self.select([(meal.pk, "2")])
        result = shopping.calculate(self.owner)
        self.assertIsNone(result["lines"][0]["buy"])
        self.assertTrue(result["lines"][0]["unknown"])
        self.assertFalse(result["selected"][0]["ready"])
        self.assertIn("Check Eggs", result["selected"][0]["reason"])

    def test_ranking_ready_first_fewer_shortages_then_coverage_and_id(self):
        ready = self.recipe("Ready", 1, default="1")
        tie_one = self.recipe("Tie A", 2, default="1")
        tie_two = self.recipe("Tie B", 2, default="1")
        less_covered = self.recipe("Less covered", 4, default="1")
        self.stock(self.eggs, 1000)
        self.assertEqual(
            [row["recipe"].pk for row in suggestions.ranked(self.owner)],
            [ready.pk, tie_one.pk, tie_two.pk, less_covered.pk],
        )

    def test_incomplete_recipe_is_saved_but_excluded(self):
        empty = recipes.save_recipe(self.owner, title="Empty", default_servings="1")
        incompatible = recipes.save_recipe(
            self.owner,
            title="Cups of rice",
            default_servings="1",
            lines=[{"ingredient": self.rice, "amount": "1", "unit": "us_cup"}],
        )
        self.assertEqual(suggestions.ranked(self.owner), [])
        self.assertIsNone(incompatible.ingredients.get().amount)
        for meal in [empty, incompatible]:
            with self.assertRaises(DomainError):
                recipes.requirements(self.owner, meal)
            with self.assertRaises(DomainError):
                self.select([(meal.pk, "1")])

    def test_fewer_than_three_favorites_and_membership_survive_updates(self):
        meal = self.recipe("One favorite", 2)
        chosen = suggestions.selection(self.owner)
        suggestions.refresh(self.owner, chosen.revision)
        self.assertEqual(
            list(chosen.entries.values_list("recipe_id", flat=True)), [meal.pk]
        )
        self.stock(self.eggs, 10000)
        recipes.save_recipe(
            self.owner,
            recipe_id=meal.pk,
            expected_revision=meal.revision,
            title=meal.title,
            default_servings="2",
            lines=[{"ingredient": self.eggs, "amount": "4", "unit": "count"}],
        )
        data = shopping.calculate(self.owner)
        self.assertEqual(data["selected"][0]["entry"].recipe_id, meal.pk)
        self.assertTrue(data["selected"][0]["changed"])
        self.assertEqual(data["lines"][0]["target"], 4000)

    def test_stale_recipe_and_shortlist_edits_are_rejected(self):
        meal = self.recipe("Eggs", 2)
        recipes.save_recipe(
            self.owner,
            recipe_id=meal.pk,
            expected_revision=meal.revision,
            title="Updated",
            default_servings="2",
        )
        with self.assertRaises(Conflict):
            recipes.save_recipe(
                self.owner,
                recipe_id=meal.pk,
                expected_revision=meal.revision,
                title="Stale",
                default_servings="2",
            )
        chosen = suggestions.selection(self.owner)
        suggestions.replace_selection(self.owner, [], chosen.revision)
        with self.assertRaises(Conflict):
            suggestions.replace_selection(self.owner, [], chosen.revision)

    def test_duplicate_selection_and_cross_owner_references_are_rejected(self):
        meal = self.recipe("Mine", 1)
        with self.assertRaises(DomainError):
            self.select([(meal.pk, "1"), (meal.pk, "2")])
        with self.assertRaises(DomainError):
            recipes.requirements(self.other, meal)
        with self.assertRaises(DomainError):
            recipes.save_recipe(
                self.other,
                title="Foreign ingredient",
                default_servings="1",
                lines=[{"ingredient": self.eggs, "amount": "1", "unit": "count"}],
            )
        chosen = suggestions.selection(self.other)
        with self.assertRaises(DomainError):
            suggestions.replace_selection(self.other, [(meal.pk, "1")], chosen.revision)


@override_settings(ALLOWED_HOSTS=["testserver"])
class RecipeInterfaceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("recipe-ui")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")
        self.client.force_login(self.owner)

    def test_create_edit_select_and_change_servings_through_forms(self):
        page = self.client.get(reverse("recipe_new"))
        self.assertEqual(page.status_code, 200)
        data = {
            "edit_token": page.context["edit_token"],
            "title": "Eggs for breakfast",
            "default_servings": "2",
            "favorite": "on",
            "instructions": "Cook gently.",
            "ingredients-TOTAL_FORMS": "1",
            "ingredients-INITIAL_FORMS": "0",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "100",
            "ingredients-0-ingredient": str(self.eggs.pk),
            "ingredients-0-amount": "3",
            "ingredients-0-unit": "count",
        }
        saved = self.client.post(reverse("recipe_new"), data)
        self.assertEqual(saved.status_code, 302)
        self.assertContains(self.client.get(saved.url), "Eggs for breakfast")
        self.assertContains(self.client.get(reverse("recipes")), "Eggs for breakfast")
        page = self.client.get(reverse("shopping"))
        result = self.client.post(
            reverse("selection_edit"),
            {"selection_token": page.context["selection_token"], "action": "refresh"},
        )
        self.assertEqual(result.status_code, 302)
        page = self.client.get(reverse("shopping"))
        self.assertContains(page, "Check how much you have")
        recipe_id = page.context["selected"][0]["entry"].recipe_id
        self.client.post(
            reverse("selection_edit"),
            {
                "selection_token": page.context["selection_token"],
                "action": "servings",
                "current": recipe_id,
                "servings": "1",
            },
        )
        self.assertEqual(shopping.calculate(self.owner)["lines"][0]["target"], 1500)
        self.assertEqual(PantryItem.objects.count(), 0)
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_recipe_and_selection_require_authentication_and_post(self):
        self.client.logout()
        for route in ["recipes", "recipe_new", "shopping"]:
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("selection_edit")).status_code, 405)

    def test_admin_recipe_edit_uses_revision_service_and_keeps_ingredients(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        meal = recipes.save_recipe(
            self.owner,
            title="Admin eggs",
            default_servings="2",
            lines=[{"ingredient": self.eggs, "amount": "3", "unit": "count"}],
        )
        url = reverse("admin:pantry_recipe_change", args=[meal.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        token = page.context["adminform"].form.fields["revision_token"].initial
        saved = self.client.post(
            url,
            {
                "title": "Admin changed",
                "servings": "3",
                "favorite": "on",
                "instructions": "New instructions",
                "revision_token": token,
                "_save": "Save",
            },
        )
        self.assertEqual(saved.status_code, 302)
        meal.refresh_from_db()
        self.assertEqual(meal.revision, 2)
        self.assertEqual(meal.default_servings, 3000)
        self.assertEqual(meal.ingredients.get().amount, 3000)

    def test_admin_catalog_creation_binds_owner_before_validation(self):
        self.owner.is_staff = self.owner.is_superuser = True
        self.owner.save()
        response = self.client.post(
            reverse("admin:pantry_ingredient_add"),
            {"name": "Milk", "dimension": "volume", "_save": "Save"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.owner.ingredient_set.filter(name="Milk").exists())
