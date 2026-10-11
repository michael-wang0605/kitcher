import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from pantry.models import (
    ImportDraft,
    ImportJob,
    Ingredient,
    InventoryEvent,
    InventoryEventLine,
    PantryItem,
    PantryState,
    Recipe,
    RecipeSelection,
)
from pantry.services import (
    backups,
    catalog,
    cooking,
    inventory,
    recipes,
    recovery,
    suggestions,
)
from pantry.services.quantities import DomainError


def representative_domain(owner):
    eggs = catalog.save_ingredient(
        owner, name="Eggs", dimension="count", aliases=["large eggs"]
    )
    rice = catalog.save_ingredient(owner, name="Rice", dimension="mass")
    catalog.add_package(owner, eggs, "carton", "6", "count")

    def apply(operations):
        return inventory.confirm(owner, inventory.preview(owner, operations)["token"])

    first = apply(
        [
            inventory.operation(eggs, "set", 10000),
            inventory.operation(rice, "set", None),
        ]
    )
    purchase = apply([inventory.operation(eggs, "add", 4000)])
    meal = recipes.save_recipe(
        owner,
        title="Eggs",
        default_servings="1",
        instructions="Cook gently.",
        lines=[{"ingredient": eggs, "amount": "2", "unit": "count"}],
    )
    recipes.save_recipe(
        owner,
        title="Unresolved rice",
        default_servings="1",
        lines=[{"ingredient": rice, "amount": "", "unit": "g"}],
    )
    selection = suggestions.selection(owner)
    suggestions.replace_selection(owner, [(meal.pk, "2")], selection.revision)
    inventory.confirm(
        owner, cooking.preview(owner, meal, "1", actual={eggs.pk: 1500})["token"]
    )
    inventory.confirm(owner, cooking.reversal_preview(owner, purchase)["token"])
    inventory.confirm(
        owner,
        cooking.correction_preview(
            owner, first, {eggs.pk: 9000, rice.pk: None}, "Recounted."
        )["token"],
    )
    # Version 1 cooking snapshot must survive an edit to the current recipe.
    recipes.save_recipe(
        owner,
        recipe_id=meal.pk,
        expected_revision=meal.revision,
        title="Updated eggs",
        default_servings="1",
        lines=[{"ingredient": eggs, "amount": "3", "unit": "count"}],
    )
    return eggs, rice


class PortableRecoveryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            "source-owner", password="source-secret"
        )
        self.target = get_user_model().objects.create_user(
            "restore-owner", password="different-secret"
        )
        self.eggs, self.rice = representative_domain(self.owner)
        ImportDraft.objects.create(
            owner=self.owner, source_type="text", source_text="private draft"
        )
        self.payload = recovery.dump_domain(self.owner)

    def test_roundtrip_preserves_domain_and_allows_new_reviewed_change(self):
        expected_password = self.target.password
        counts = recovery.load_domain(self.target, self.payload)
        self.assertEqual(counts["events"], 5)
        stock = {
            item.ingredient.name: item.quantity
            for item in PantryItem.objects.filter(owner=self.target).select_related(
                "ingredient"
            )
        }
        self.assertEqual(stock, {"Eggs": 9000, "Rice": None})
        events = InventoryEvent.objects.filter(owner=self.target)
        self.assertEqual(events.filter(reversal_of__isnull=False).count(), 1)
        self.assertEqual(events.filter(correction_of__isnull=False).count(), 1)
        original = InventoryEvent.objects.get(owner=self.owner, source="cooking")
        restored = events.get(source="cooking")
        self.assertEqual(restored.recipe_snapshot, original.recipe_snapshot)
        self.assertEqual(restored.created_at, original.created_at)
        self.assertEqual(restored.command_hash, original.command_hash)
        self.assertEqual(restored.request_id, original.request_id)
        self.assertEqual(
            RecipeSelection.objects.get(owner=self.target).entries.get().recipe.title,
            "Updated eggs",
        )
        self.assertEqual(ImportDraft.objects.filter(owner=self.target).count(), 0)
        self.target.refresh_from_db()
        self.assertEqual(self.target.password, expected_password)
        egg = Ingredient.objects.get(owner=self.target, name="Eggs")
        self.assertNotEqual(egg.pk, self.eggs.pk)
        change = inventory.confirm(
            self.target,
            inventory.preview(
                self.target, [inventory.operation(egg, "subtract", 1000)]
            )["token"],
        )
        self.assertEqual(change.revision_after, 6)
        self.assertEqual(
            PantryItem.objects.get(owner=self.target, ingredient=egg).quantity, 8000
        )
        # Restored data can itself be exported and imported again.
        repeated = get_user_model().objects.create_user("second-restore")
        recovery.load_domain(repeated, recovery.dump_domain(self.target))
        self.assertEqual(PantryState.objects.get(owner=repeated).revision, 6)

    def test_status_requires_login_and_reports_backup_failure(self):
        from django.urls import reverse

        self.assertEqual(self.client.get(reverse("status")).status_code, 302)
        self.client.force_login(self.owner)
        with TemporaryDirectory() as temp, override_settings(VAR_DIR=Path(temp)):
            backups.atomic_json(
                Path(temp) / "backup-status.json",
                {
                    "last_success": "2026-10-08T12:00:00+00:00",
                    "mirror_error": "Unavailable backup disk.",
                },
            )
            response = self.client.get(reverse("status"))
        self.assertContains(response, "Unavailable backup disk.")
        self.assertContains(response, "2026-10-08T12:00:00+00:00")

    def test_export_excludes_other_owner_credentials_drafts_and_jobs(self):
        catalog.save_ingredient(self.target, name="Other account", dimension="volume")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "pantry.json"
            recovery.write_export(self.owner, path)
            payload = recovery.read_export(path)
        encoded = json.dumps(payload)
        for prohibited in (
            "password",
            "session",
            "private draft",
            "Other account",
            "source-owner",
        ):
            self.assertNotIn(prohibited, encoded)
        self.assertEqual(len(payload["tables"]["ingredients"]), 2)

    def test_invalid_fields_references_balances_links_and_types_write_nothing(self):
        changes = [
            lambda p: p.update(schema_version=2),
            lambda p: p.update(pantry_revision=True),
            lambda p: p["tables"]["items"][0].update(quantity=-1),
            lambda p: p["tables"]["items"][0].update(quantity=1234),
            lambda p: p["tables"]["aliases"][0].update(ingredient=987654321),
            lambda p: p["tables"]["events"][0].update(
                reversal_of=p["tables"]["events"][-1]["id"]
            ),
            lambda p: p["tables"]["events"][0].update(request_id="bad-uuid"),
            lambda p: p["tables"]["event_lines"][0]["operations"][0].update(amount=-1),
            lambda p: p["tables"]["recipes"][0].update(favorite="false"),
            lambda p: p["tables"]["recipe_lines"][0].update(position=2**40),
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                payload = copy.deepcopy(self.payload)
                change(payload)
                with self.assertRaises(DomainError):
                    recovery.load_domain(self.target, payload)
                self.assertFalse(Ingredient.objects.filter(owner=self.target).exists())
                self.assertFalse(
                    RecipeSelection.objects.filter(owner=self.target).exists()
                )

    def test_late_restore_failure_rolls_back_all_domain_records(self):
        original = InventoryEventLine.save
        calls = 0

        def injected(record, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == len(self.payload["tables"]["event_lines"]):
                raise RuntimeError("injected final history failure")
            return original(record, *args, **kwargs)

        with (
            patch.object(InventoryEventLine, "save", injected),
            self.assertRaises(RuntimeError),
        ):
            recovery.load_domain(self.target, self.payload)
        self.assertFalse(Ingredient.objects.filter(owner=self.target).exists())
        self.assertFalse(Recipe.objects.filter(owner=self.target).exists())
        self.assertFalse(InventoryEvent.objects.filter(owner=self.target).exists())
        self.assertFalse(RecipeSelection.objects.filter(owner=self.target).exists())

    def test_existing_account_domain_is_never_replaced(self):
        item = catalog.save_ingredient(self.target, name="Existing", dimension="count")
        with self.assertRaises(DomainError):
            recovery.load_domain(self.target, self.payload)
        self.assertEqual(
            list(
                Ingredient.objects.filter(owner=self.target).values_list(
                    "pk", flat=True
                )
            ),
            [item.pk],
        )

    def test_corrupt_cooking_snapshot_and_reversal_semantics_are_rejected(self):
        payload = copy.deepcopy(self.payload)
        event = next(
            row for row in payload["tables"]["events"] if row["source"] == "cooking"
        )
        event["recipe_snapshot"]["ingredients"][0]["required"] = -1
        with self.assertRaises(DomainError):
            recovery.load_domain(self.target, payload)
        payload = copy.deepcopy(self.payload)
        event = next(
            row for row in payload["tables"]["events"] if row["source"] == "reversal"
        )
        event["source"] = "manual"
        with self.assertRaises(DomainError):
            recovery.load_domain(self.target, payload)
        self.assertFalse(Ingredient.objects.filter(owner=self.target).exists())

    def test_worker_stop_marker_prevents_new_claims(self):
        with TemporaryDirectory() as temp, override_settings(VAR_DIR=Path(temp)):
            Path(temp, "import-worker.stop").write_text("stop")
            with (
                patch("pantry.services.import_worker.cleanup"),
                patch("pantry.services.import_worker.process_one") as process,
            ):
                call_command("process_imports", "--once", verbosity=0)
                process.assert_not_called()

    def test_duplicate_json_keys_nonfinite_and_oversized_files_rejected(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            for text in (
                '{"schema_version":1,"schema_version":2}',
                '{"x": NaN}',
                '{"x": Infinity}',
            ):
                path.write_text(text)
                with self.assertRaises(DomainError):
                    recovery.read_export(path)
            with patch.object(recovery, "MAX_BYTES", 10):
                path.write_text(" " * 11)
                with self.assertRaises(DomainError):
                    recovery.read_export(path)

    def test_missing_sources_expire_processing_but_keep_reviewed_data(self):
        with TemporaryDirectory() as temp, override_settings(VAR_DIR=Path(temp)):
            for status in ("processing", "needs_review"):
                draft = ImportDraft.objects.create(
                    owner=self.target,
                    source_type="fridge",
                    status=status,
                    candidates=[{"evidence": "reviewed"}]
                    if status == "needs_review"
                    else [],
                )
                ImportJob.objects.create(
                    owner=self.target,
                    draft=draft,
                    source_name=f"{status}.jpg",
                    fingerprint="a" * 64,
                    expires_at=timezone.now() + timedelta(hours=1),
                    state="processing",
                )
            call_command("recover_imports", verbosity=0)
            self.assertEqual(
                ImportDraft.objects.get(
                    owner=self.target, status="needs_review"
                ).candidates,
                [{"evidence": "reviewed"}],
            )
            self.assertEqual(
                ImportDraft.objects.filter(owner=self.target, status="expired").count(),
                1,
            )
            self.assertEqual(
                ImportJob.objects.filter(owner=self.target, state="expired").count(), 2
            )


class BackupRecoveryTests(TransactionTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = override_settings(
            VAR_DIR=self.root / "var",
            BACKUP_DIR=self.root / "backups",
            BACKUP_MIRROR=str(self.root / "mirror"),
        )
        (self.root / "var").mkdir()
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.owner = get_user_model().objects.create_user("backup-owner")
        self.eggs, self.rice = representative_domain(self.owner)

    def test_concurrent_imports_only_one_can_claim_a_fresh_account(self):
        payload = recovery.dump_domain(self.owner)
        target = get_user_model().objects.create_user("race-restore")
        barrier = Barrier(2)

        def attempt():
            connections.close_all()
            try:
                owner = get_user_model().objects.get(pk=target.pk)
                barrier.wait(timeout=10)
                try:
                    recovery.load_domain(owner, payload)
                    return "imported"
                except DomainError:
                    return "refused"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), ["imported", "refused"])
        self.assertEqual(InventoryEvent.objects.filter(owner=target).count(), 5)

    def test_verified_backup_mirror_and_independent_database_restore(self):
        status = backups.create_backup()
        self.assertFalse(status["mirror_error"])
        source = Path(status["path"])
        self.assertEqual(backups.digest(source), backups.digest(status["mirror_path"]))
        destination = self.root / "restored" / "kitcher.sqlite3"
        backups.restore_backup(source, destination)
        with closing(sqlite3.connect(destination)) as db:
            stock = db.execute(
                "SELECT i.name,p.quantity FROM pantry_pantryitem p JOIN pantry_ingredient i ON i.id=p.ingredient_id ORDER BY i.name"
            ).fetchall()
            self.assertEqual(stock, [("Eggs", 9000), ("Rice", None)])
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM pantry_inventoryevent").fetchone()[0],
                5,
            )
        self.assertNotEqual(
            destination.resolve(), Path(connection.settings_dict["NAME"]).resolve()
        )
        with self.assertRaises(DomainError):
            backups.restore_backup(source, destination)

    def test_corrupt_backup_or_manifest_is_refused(self):
        source = Path(backups.create_backup()["path"])
        manifest = source.with_suffix(".json")
        data = json.loads(manifest.read_text())
        data["sha256"] = "0" * 64
        manifest.write_text(json.dumps(data))
        target = self.root / "failed.sqlite3"
        with self.assertRaises(DomainError):
            backups.restore_backup(source, target)
        self.assertFalse(target.exists())

    def test_backup_replaced_between_validation_and_copy_is_refused(self):
        source = Path(backups.create_backup()["path"])
        original = backups.mirror_backup

        def changed(original_path, target, expected):
            with original_path.open("ab") as stream:
                stream.write(b"changed after validation")
            return original(original_path, target, expected)

        destination = self.root / "changed-restore.sqlite3"
        with (
            patch.object(backups, "mirror_backup", changed),
            self.assertRaises(DomainError),
        ):
            backups.restore_backup(source, destination)
        self.assertFalse(destination.exists())

    def test_retention_keeps_seven_daily_and_pre_migration_backups(self):
        for days in range(10):
            with patch.object(
                backups.timezone,
                "now",
                return_value=timezone.now() - timedelta(days=days),
            ):
                backups.create_backup()
        self.assertEqual(
            len(list((self.root / "backups" / "daily").glob("*.sqlite3"))), 7
        )
        self.assertEqual(
            len(list((self.root / "mirror" / "daily").glob("*.sqlite3"))), 7
        )
        status = backups.create_backup(pre_migration=True)
        self.assertTrue(Path(status["path"]).exists())
        backups.create_backup()
        self.assertTrue(Path(status["path"]).exists())

    def test_failure_preserves_previous_backup_and_reports_mirror_failure(self):
        first = backups.create_backup()
        original_hash = backups.digest(first["path"])
        with (
            patch.object(
                backups,
                "inspect_database",
                side_effect=DomainError("injected validation"),
            ),
            self.assertRaises(DomainError),
        ):
            backups.create_backup()
        self.assertEqual(backups.digest(first["path"]), original_hash)
        self.assertTrue(backups.read_status()["error"])
        with patch.object(
            backups, "mirror_backup", side_effect=OSError("unavailable mirror")
        ):
            failed_mirror = backups.create_backup()
        self.assertTrue(failed_mirror["mirror_error"])
        self.assertEqual(
            backups.inspect_database(failed_mirror["path"])["sha256"],
            failed_mirror["sha256"],
        )
        self.assertFalse(list((self.root / "backups" / "daily").glob("*.partial")))
