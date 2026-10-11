from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from pantry.models import (
    IngredientAlias,
    InventoryEvent,
    InventoryEventLine,
    PantryItem,
    PantryState,
)
from pantry.services import catalog, inventory
from pantry.services.quantities import Conflict, DomainError


class InventoryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "owner", password="test-password"
        )
        self.other = get_user_model().objects.create_user("other")
        self.eggs = catalog.save_ingredient(
            self.owner, name="Eggs", dimension="count", aliases=["large eggs"]
        )

    def make_preview(self, kind, amount, **kwargs):
        return inventory.preview(
            self.owner, [inventory.operation(self.eggs, kind, amount, **kwargs)]
        )

    def apply(self, kind, amount, **kwargs):
        return inventory.confirm(
            self.owner, self.make_preview(kind, amount, **kwargs)["token"]
        )

    def test_purchase_use_and_observation_all_record_history(self):
        self.apply("set", 0)
        purchase = self.apply("add", 6000)
        self.apply("subtract", 2000)
        self.apply("set", 3000)
        self.assertEqual(PantryItem.objects.get().quantity, 3000)
        self.assertEqual(InventoryEvent.objects.count(), 4)
        self.assertEqual(purchase.lines.get().delta, 6000)
        self.assertEqual(PantryState.objects.get().revision, 4)

    def test_unknown_is_not_zero_and_reconciliation_is_logged(self):
        with self.assertRaises(DomainError):
            self.make_preview("add", 6000)
        event = self.apply("add", 6000, reconcile=2000)
        line = event.lines.get()
        self.assertIsNone(line.before)
        self.assertIsNone(line.delta)
        self.assertEqual(line.after, 8000)
        self.assertEqual(line.operations[0]["reconcile"], 2000)
        self.apply("set", None)
        self.assertIsNone(PantryItem.objects.get().quantity)

    def test_repeated_confirmation_returns_original_even_after_another_update(self):
        reviewed = self.make_preview("set", 6000)
        event = inventory.confirm(self.owner, reviewed["token"])
        self.apply("subtract", 2000)
        repeated = inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(event.pk, repeated.pk)
        self.assertEqual(PantryItem.objects.get().quantity, 4000)
        self.assertEqual(InventoryEvent.objects.count(), 2)

    def test_uuid_reuse_with_different_command_is_rejected(self):
        reviewed = self.make_preview("set", 6000)
        inventory.confirm(self.owner, reviewed["token"])
        payload = reviewed["payload"]
        payload["operations"][0]["amount"] = 9000
        changed = signing.dumps(payload, salt=inventory.SALT)
        with self.assertRaises(DomainError):
            inventory.confirm(self.owner, changed)
        self.assertEqual(PantryItem.objects.get().quantity, 6000)

    def test_stale_preview_and_invalid_deduction_write_nothing(self):
        reviewed = self.make_preview("set", 6000)
        self.apply("set", 3000)
        with self.assertRaises(Conflict):
            inventory.confirm(self.owner, reviewed["token"])
        with self.assertRaises(DomainError):
            self.make_preview("subtract", 4000)
        self.assertEqual(PantryItem.objects.get().quantity, 3000)
        self.assertEqual(PantryState.objects.get().revision, 1)
        self.assertEqual(InventoryEvent.objects.count(), 1)

    def test_multi_ingredient_failure_rolls_back_everything(self):
        rice = catalog.save_ingredient(self.owner, name="Rice", dimension="mass")
        reviewed = inventory.preview(
            self.owner,
            [
                inventory.operation(self.eggs, "set", 1000),
                inventory.operation(rice, "set", 2000),
            ],
        )
        original = PantryItem.save

        def fail_on_rice(item, *args, **kwargs):
            if item.ingredient_id == rice.pk and item.quantity is not None:
                raise RuntimeError("Injected failure on last balance")
            return original(item, *args, **kwargs)

        with (
            patch.object(PantryItem, "save", fail_on_rice),
            self.assertRaises(RuntimeError),
        ):
            inventory.confirm(self.owner, reviewed["token"])
        self.assertEqual(PantryItem.objects.count(), 0)
        self.assertEqual(InventoryEvent.objects.count(), 0)
        self.assertEqual(InventoryEventLine.objects.count(), 0)
        self.assertEqual(PantryState.objects.get().revision, 0)

    def test_alias_normalization_and_collision(self):
        self.assertTrue(
            IngredientAlias.objects.filter(normalized_name="large eggs").exists()
        )
        with self.assertRaises(DomainError):
            catalog.save_ingredient(self.owner, name="LARGE  EGGS", dimension="count")
        self.assertEqual(self.owner.ingredient_set.count(), 1)

    def test_cross_owner_and_signed_token_tampering_are_rejected(self):
        with self.assertRaises(DomainError):
            inventory.preview(self.other, [inventory.operation(self.eggs, "set", 1000)])
        reviewed = self.make_preview("set", 1000)
        with self.assertRaises(DomainError):
            inventory.confirm(self.other, reviewed["token"])
        with self.assertRaises(DomainError):
            inventory.confirm(self.owner, reviewed["token"] + "x")
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_event_and_lines_reject_edits(self):
        event = self.apply("set", 1000)
        event.source = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        line = event.lines.get()
        line.after = 2000
        with self.assertRaises(ValidationError):
            line.save()


class ContentionTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("race-owner")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")

    def race(self, tokens):
        barrier = Barrier(2)

        def worker(token):
            try:
                user = get_user_model().objects.get(pk=self.owner.pk)
                barrier.wait(timeout=10)
                try:
                    return ("ok", inventory.confirm(user, token).pk)
                except Conflict:
                    return ("conflict", None)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, tokens))

    def test_simultaneous_identical_confirmations_create_one_event_and_row(self):
        self.assertNotEqual(connections["default"].settings_dict["NAME"], ":memory:")
        reviewed = inventory.preview(
            self.owner, [inventory.operation(self.eggs, "set", 6000)]
        )
        result = self.race([reviewed["token"], reviewed["token"]])
        self.assertEqual(result[0], result[1])
        self.assertEqual(InventoryEvent.objects.count(), 1)
        self.assertEqual(PantryItem.objects.count(), 1)
        self.assertEqual(PantryItem.objects.get().quantity, 6000)
        self.assertEqual(PantryState.objects.get().revision, 1)

    def test_different_previews_at_same_revision_commit_only_one(self):
        first = inventory.preview(
            self.owner, [inventory.operation(self.eggs, "set", 6000)]
        )
        second = inventory.preview(
            self.owner, [inventory.operation(self.eggs, "set", 9000)]
        )
        result = self.race([first["token"], second["token"]])
        self.assertEqual(sorted(status for status, _ in result), ["conflict", "ok"])
        self.assertEqual(InventoryEvent.objects.count(), 1)
        self.assertEqual(PantryItem.objects.count(), 1)
        self.assertEqual(PantryState.objects.get().revision, 1)


@override_settings(ALLOWED_HOSTS=["testserver"])
class InterfaceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "ui-owner", password="test-password"
        )
        self.other = get_user_model().objects.create_user(
            "ui-other", password="test-password"
        )
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")

    def test_authentication_owner_scope_and_full_review_flow(self):
        for route in ["pantry", "ingredients", "history"]:
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("preview"),
            {"ingredient": self.eggs.pk, "kind": "set", "amount": "6", "unit": "count"},
        )
        self.assertEqual(response.status_code, 200)
        token = response.context["preview"]["token"]
        self.assertContains(response, "Unknown")
        confirmed = self.client.post(reverse("confirm"), {"token": token})
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(PantryItem.objects.get().quantity, 6000)
        self.assertContains(self.client.get(reverse("pantry")), "6 count")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("pantry")), "Eggs")
        self.assertEqual(self.client.get(confirmed.url).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("preview"),
                {
                    "ingredient": self.eggs.pk,
                    "kind": "set",
                    "amount": "7",
                    "unit": "count",
                },
            ).status_code,
            422,
        )

    def test_stale_confirmation_shows_fresh_review_and_requires_another_post(self):
        self.client.force_login(self.owner)
        reviewed = inventory.preview(
            self.owner, [inventory.operation(self.eggs, "set", 6000)]
        )
        inventory.confirm(
            self.owner,
            inventory.preview(
                self.owner, [inventory.operation(self.eggs, "set", 3000)]
            )["token"],
        )
        response = self.client.post(reverse("confirm"), {"token": reviewed["token"]})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Inventory changed", status_code=409)
        self.assertEqual(PantryItem.objects.get().quantity, 3000)
        self.assertNotEqual(response.context["preview"]["token"], reviewed["token"])

    def test_confirmation_requires_post_and_csrf(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("confirm")).status_code, 405)
        from django.test import Client

        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner)
        self.assertEqual(
            protected.post(reverse("confirm"), {"token": "x"}).status_code, 403
        )
