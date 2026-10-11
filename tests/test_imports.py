import io
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from pantry.import_views import SALT, revision_token
from pantry.models import ImportDraft, ImportJob, InventoryEvent, PantryItem
from pantry.services import candidates, catalog, import_worker, imports, inventory
from pantry.services.quantities import Conflict, DomainError


def image_upload(image_format="JPEG", *, orientation=None):
    image = Image.new("RGB", (20, 10), "white")
    stream = io.BytesIO()
    kwargs = {}
    if image_format == "MPO":
        kwargs.update(save_all=True, append_images=[Image.new("RGB", (2, 2))])
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(stream, format=image_format, **kwargs)
    return SimpleUploadedFile(
        "private-photo.jpg", stream.getvalue(), content_type="application/octet-stream"
    )


def raw(amount="6", unit="count", *, name="Eggs", uncertain=False):
    return {
        "schema": 1,
        "items": [
            {
                "name": name,
                "amount": amount,
                "unit": unit,
                "evidence": "Six visible eggs.",
                "uncertain": uncertain,
            }
        ],
    }


class ImportFixture:
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            VAR_DIR=Path(self.directory.name), ALLOWED_HOSTS=["testserver"]
        )
        self.settings_override.enable()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.settings_override.disable)
        self.owner = get_user_model().objects.create_user("import-owner")
        self.other = get_user_model().objects.create_user("import-other")
        self.eggs = catalog.save_ingredient(
            self.owner, name="Eggs", dimension="count", aliases=["large eggs"]
        )
        self.rice = catalog.save_ingredient(self.owner, name="Rice", dimension="mass")

    def row(self, **kwargs):
        return {
            "ingredient": self.eggs.pk,
            "kind": "set",
            "amount": "6",
            "unit": "count",
            "reconcile": "",
            "evidence": "Reviewed manually.",
            **kwargs,
        }

    def photo(self, source_type="fridge"):
        return imports.create_photo(self.owner, image_upload(), source_type)

    def reviewed_photo(self, source_type="fridge"):
        draft = self.photo(source_type)
        job = import_worker.claim()
        self.assertTrue(import_worker.publish(job, raw=raw()))
        draft.refresh_from_db()
        return self.edit(draft)

    def edit(self, draft, rows=None, **kwargs):
        return imports.edit(
            self.owner, draft.pk, draft.revision, rows or [self.row()], **kwargs
        )


class CandidateTests(ImportFixture, TestCase):
    def test_explicit_text_operations_and_units(self):
        draft = imports.create_text(
            self.owner, "bought six eggs; used 200 g rice; have three eggs left"
        )
        self.assertEqual(
            [r["kind"] for r in draft.candidates], ["add", "subtract", "set"]
        )
        self.assertEqual([r["amount"] for r in draft.candidates], ["6", "200", "3"])
        self.assertTrue(all(not r["unresolved"] for r in draft.candidates))
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_ambiguous_text_and_missing_units_stay_unresolved(self):
        for text in [
            "half bag of rice",
            "bought half bag of rice",
            "have six eggs",
            "bought 200 rice",
            "used a few eggs",
        ]:
            draft = imports.create_text(self.owner, text)
            self.assertTrue(draft.candidates[0]["unresolved"], text)
            with self.assertRaises(DomainError):
                imports.preview(self.owner, draft.pk)

    def test_package_fraction_requires_one_matching_saved_size(self):
        catalog.add_package(
            self.owner, ingredient=self.rice, label="rice bag", amount="1000", unit="g"
        )
        result = candidates.parse_text(self.owner, "used half bag of rice")
        self.assertEqual(result[0]["amount"], "500")
        self.assertEqual(result[0]["unit"], "g")
        self.assertEqual(result[0]["unresolved"], "")
        catalog.add_package(
            self.owner, ingredient=self.rice, label="small bag", amount="250", unit="g"
        )
        self.assertTrue(
            candidates.parse_text(self.owner, "used half bag of rice")[0]["unresolved"]
        )

    def test_alias_matching_uncertainty_and_unknown_items(self):
        self.assertEqual(
            candidates.validate_raw(self.owner, raw(name="LARGE  EGGS"), "fridge")[0][
                "ingredient"
            ],
            self.eggs.pk,
        )
        for value in [
            raw(uncertain=True),
            raw(amount=None, unit=None),
            raw(name="Mystery"),
        ]:
            rows = candidates.validate_raw(self.owner, value, "fridge")
            self.assertTrue(rows[0]["unresolved"])
        rows = candidates.validate_raw(
            self.owner, raw(amount=None, unit=None), "receipt"
        )
        self.assertEqual(rows[0]["kind"], "add")

    def test_recognized_quantity_needs_explicit_review_even_with_known_alias(self):
        rows = candidates.validate_raw(self.owner, raw(), "fridge")
        self.assertEqual(rows[0]["ingredient"], self.eggs.pk)
        self.assertIn("Confirm the recognized ingredient amount", rows[0]["unresolved"])

    def test_invalid_model_fields_never_reach_inventory(self):
        values = [
            raw("-1"),
            raw("NaN"),
            raw("Infinity"),
            raw("6", "invented"),
            raw(amount=6),
            {"schema": 2, "items": []},
            {"schema": 1, "items": [None]},
        ]
        for value in values:
            with self.assertRaises(DomainError):
                candidates.validate_raw(self.owner, value, "fridge")
        self.assertEqual(InventoryEvent.objects.count(), 0)


class UploadTests(ImportFixture, TestCase):
    def test_decodes_actual_format_and_normalizes_orientation(self):
        draft = imports.create_photo(self.owner, image_upload(orientation=6), "fridge")
        job = draft.job
        with Image.open(imports.source_path(job.source_name)) as image:
            self.assertEqual(image.size, (10, 20))
            self.assertEqual(image.format, "JPEG")
            self.assertNotIn(274, image.getexif())
        self.assertEqual(job.source_name, f"{Path(job.source_name).stem}.jpg")
        self.assertTrue(len(draft.fingerprint) == 64)
        self.assertEqual(draft.status, "processing")

    def test_phone_jpeg_with_auxiliary_frame_is_normalized_as_one_image(self):
        draft = imports.create_photo(self.owner, image_upload("MPO"), "fridge")
        with Image.open(imports.source_path(draft.job.source_name)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (20, 10))
            self.assertEqual(getattr(image, "n_frames", 1), 1)

    def test_rejects_unsupported_oversized_and_corrupt_uploads(self):
        for upload in [
            image_upload("GIF"),
            SimpleUploadedFile("x.jpg", b"not an image"),
        ]:
            with self.assertRaises(DomainError):
                imports.create_photo(self.owner, upload, "fridge")
        upload = image_upload()
        upload.size = imports.MAX_BYTES + 1
        with self.assertRaises(DomainError):
            imports.create_photo(self.owner, upload, "fridge")
        with patch.object(imports, "MAX_PIXELS", 100), self.assertRaises(DomainError):
            self.photo()
        self.assertEqual(ImportDraft.objects.count(), 0)

    def test_database_failure_cleans_written_source_and_path_escape_is_rejected(self):
        with (
            patch.object(
                ImportJob.objects, "create", side_effect=RuntimeError("failure")
            ),
            self.assertRaises(RuntimeError),
        ):
            self.photo()
        self.assertEqual(list(imports.spool().glob("*.jpg")), [])
        for name in ["../original.jpg", "C:/original.jpg", "photo.png"]:
            with self.assertRaises(DomainError):
                imports.source_path(name)

    def test_duplicate_warning_is_owner_scoped_and_needs_explicit_ack(self):
        first, second = self.photo(), self.photo()
        self.assertFalse(first.duplicate_source)
        self.assertTrue(second.duplicate_source)
        other = imports.create_photo(self.other, image_upload(), "fridge")
        self.assertFalse(other.duplicate_source)
        second = self.edit(second)
        with self.assertRaises(DomainError):
            imports.preview(self.owner, second.pk)
        second = self.edit(second, duplicate_ack=True)
        self.assertTrue(imports.preview(self.owner, second.pk)["token"])


class DraftTests(ImportFixture, TestCase):
    def test_photo_sets_visible_items_and_leaves_unseen_rice_unchanged(self):
        inventory.confirm(
            self.owner,
            inventory.preview(
                self.owner, [inventory.operation(self.rice, "set", 500000)]
            )["token"],
        )
        draft = self.reviewed_photo()
        preview = imports.preview(self.owner, draft.pk)
        with self.captureOnCommitCallbacks(execute=True):
            event = inventory.confirm(self.owner, preview["token"])
        draft.refresh_from_db()
        self.assertEqual(draft.applied_event_id, event.pk)
        self.assertEqual(draft.status, "applied")
        self.assertEqual(PantryItem.objects.get(ingredient=self.eggs).quantity, 6000)
        self.assertEqual(PantryItem.objects.get(ingredient=self.rice).quantity, 500000)
        self.assertFalse(imports.source_path(draft.job.source_name).exists())
        self.assertEqual(inventory.confirm(self.owner, preview["token"]).pk, event.pk)

    def test_receipt_does_not_assume_zero_and_review_cannot_change_add_to_set(self):
        draft = self.reviewed_photo("receipt")
        with self.assertRaises(DomainError):
            imports.preview(self.owner, draft.pk)
        draft = self.edit(draft, [self.row(kind="set", reconcile="2")])
        event = inventory.confirm(
            self.owner, imports.preview(self.owner, draft.pk)["token"]
        )
        self.assertEqual(event.lines.get().after, 8000)
        self.assertEqual(event.lines.get().operations[0]["kind"], "add")
        self.assertIsNone(event.lines.get().before)

    def test_edit_invalidates_preview_and_refresh_uses_current_draft(self):
        draft = imports.create_text(self.owner, "have six eggs left")
        preview = imports.preview(self.owner, draft.pk)
        draft = self.edit(draft, [self.row(amount="3")])
        with self.assertRaises(Conflict):
            inventory.confirm(self.owner, preview["token"])
        fresh = inventory.refresh(self.owner, preview["token"])
        self.assertEqual(fresh["lines"][0]["after"], 3000)
        self.assertEqual(InventoryEvent.objects.count(), 0)
        inventory.confirm(self.owner, fresh["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 3000)

    def test_two_previews_apply_one_draft_only_once(self):
        draft = imports.create_text(self.owner, "bought six eggs")
        draft = self.edit(draft, [self.row(kind="add", reconcile="0")])
        first, second = (
            imports.preview(self.owner, draft.pk),
            imports.preview(self.owner, draft.pk),
        )
        inventory.confirm(self.owner, first["token"])
        with self.assertRaises(Conflict):
            inventory.confirm(self.owner, second["token"])
        self.assertEqual(InventoryEvent.objects.count(), 1)

    def test_event_failure_rolls_back_draft_claim(self):
        draft = imports.create_text(self.owner, "have six eggs left")
        preview = imports.preview(self.owner, draft.pk)
        with (
            patch.object(
                imports, "finish", side_effect=RuntimeError("last-write failure")
            ),
            self.assertRaises(RuntimeError),
        ):
            inventory.confirm(self.owner, preview["token"])
        draft.refresh_from_db()
        self.assertEqual(draft.status, "needs_review")
        self.assertEqual(InventoryEvent.objects.count(), 0)
        self.assertEqual(PantryItem.objects.count(), 0)

    def test_cancel_rejects_late_result_and_removes_only_spooled_copy(self):
        draft = self.photo()
        job = import_worker.claim()
        original = Path(self.directory.name) / "fridge" / "original.jpg"
        original.parent.mkdir()
        original.write_bytes(b"original")
        with self.captureOnCommitCallbacks(execute=True):
            imports.cancel(self.owner, draft.pk, draft.revision)
        self.assertFalse(import_worker.publish(job, raw=raw()))
        self.assertTrue(original.exists())
        self.assertFalse(imports.source_path(job.source_name).exists())

    def test_owner_isolation_and_stale_edit(self):
        draft = imports.create_text(self.owner, "have six eggs left")
        with self.assertRaises(DomainError):
            imports.preview(self.other, draft.pk)
        with self.assertRaises(DomainError):
            imports.edit(self.other, draft.pk, draft.revision, [self.row()])
        self.edit(draft)
        with self.assertRaises(Conflict):
            self.edit(draft)


class WorkerTests(ImportFixture, TestCase):
    def test_claim_and_validated_publication_do_not_mutate_stock(self):
        draft = self.photo()
        job = import_worker.claim()
        self.assertEqual(job.attempts, 1)
        self.assertIsNone(import_worker.claim())
        self.assertTrue(import_worker.publish(job, raw=raw()))
        draft.refresh_from_db()
        self.assertEqual(draft.status, "needs_review")
        self.assertEqual(draft.job.state, "succeeded")
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_lease_recovery_and_old_attempt_cannot_publish(self):
        draft = self.photo()
        first = import_worker.claim()
        later = first.lease_until + timedelta(seconds=1)
        second = import_worker.claim(now=later)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.attempts, 2)
        self.assertNotEqual(second.attempt_token, first.attempt_token)
        self.assertFalse(import_worker.publish(first, raw=raw(), now=later))
        self.assertTrue(import_worker.publish(second, raw=raw("3"), now=later))
        draft.refresh_from_db()
        self.assertEqual(draft.candidates[0]["amount"], "3")

    def test_three_attempts_use_ten_and_thirty_second_delays(self):
        draft = self.photo()
        now = timezone.now()
        first = import_worker.claim(now=now)
        import_worker.publish(first, error="offline", transient=True, now=now)
        draft.job.refresh_from_db()
        self.assertEqual(draft.job.next_attempt_at, now + timedelta(seconds=10))
        self.assertIsNone(import_worker.claim(now=now + timedelta(seconds=9)))
        second = import_worker.claim(now=now + timedelta(seconds=10))
        import_worker.publish(
            second, error="offline", transient=True, now=now + timedelta(seconds=10)
        )
        second.refresh_from_db()
        self.assertEqual(second.next_attempt_at, now + timedelta(seconds=40))
        third = import_worker.claim(now=now + timedelta(seconds=40))
        import_worker.publish(
            third, error="offline", transient=True, now=now + timedelta(seconds=40)
        )
        draft.refresh_from_db()
        self.assertEqual(draft.status, "needs_review")
        self.assertEqual(draft.job.state, "failed")
        self.assertEqual(draft.job.attempts, 3)
        self.assertTrue(draft.candidates[0]["unresolved"])

    def test_three_abandoned_leases_terminal_failure(self):
        draft = self.photo()
        now = timezone.now()
        for attempt in range(3):
            job = import_worker.claim(now=now)
            self.assertEqual(job.attempts, attempt + 1)
            now = job.lease_until + timedelta(seconds=1)
        self.assertIsNone(import_worker.claim(now=now))
        draft.refresh_from_db()
        self.assertEqual(draft.job.state, "failed")
        self.assertEqual(draft.status, "needs_review")

    def test_malformed_result_fails_without_retry_and_manual_fallback_works(self):
        draft = self.photo()
        job = import_worker.claim()
        import_worker.publish(job, raw={"wrong": []})
        draft.refresh_from_db()
        self.assertEqual(draft.job.state, "failed")
        draft = self.edit(draft)
        inventory.confirm(self.owner, imports.preview(self.owner, draft.pk)["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 6000)

    def test_manual_edit_during_processing_rejects_worker_and_can_apply(self):
        draft = self.photo()
        job = import_worker.claim()
        draft = self.edit(draft, [self.row(amount="4")])
        self.assertFalse(import_worker.publish(job, raw=raw()))
        inventory.confirm(self.owner, imports.preview(self.owner, draft.pk)["token"])
        self.assertEqual(PantryItem.objects.get().quantity, 4000)

    def test_expired_source_is_never_processed_and_review_survives_cleanup(self):
        queued = self.photo()
        ImportJob.objects.filter(draft=queued).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertIsNone(import_worker.claim())
        queued.refresh_from_db()
        self.assertEqual(queued.status, "expired")
        reviewed = self.reviewed_photo()
        ImportJob.objects.filter(draft=reviewed).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            import_worker.cleanup()
        reviewed.refresh_from_db()
        self.assertEqual(reviewed.status, "needs_review")
        reviewed = self.edit(reviewed, duplicate_ack=True)
        self.assertTrue(imports.preview(self.owner, reviewed.pk)["token"])
        self.assertFalse(imports.source_path(reviewed.job.source_name).exists())

    def test_retry_changes_revision_and_invalidates_old_attempt(self):
        draft = self.photo()
        old = import_worker.claim()
        import_worker.publish(old, error="offline", transient=False)
        draft.refresh_from_db()
        revision = draft.revision
        imports.retry(self.owner, draft.pk, revision)
        draft.refresh_from_db()
        self.assertEqual(draft.revision, revision + 1)
        self.assertEqual(draft.status, "processing")
        self.assertEqual(draft.job.attempts, 0)
        new = import_worker.claim()
        self.assertFalse(import_worker.publish(old, raw=raw()))
        self.assertTrue(import_worker.publish(new, raw=raw()))

    def test_cleanup_removes_old_orphans_but_preserves_new_uploads(self):
        old, new = imports.spool() / "orphan.jpg", imports.spool() / "new.jpg"
        old.write_bytes(b"orphan")
        new.write_bytes(b"new")
        stamp = (timezone.now() - timedelta(minutes=16)).timestamp()
        os.utime(old, (stamp, stamp))
        self.assertEqual(import_worker.cleanup(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_subprocess_timeout_and_malformed_stdout_are_safe(self):
        job = self.photo().job
        with patch(
            "pantry.services.import_worker.subprocess.run",
            side_effect=subprocess.TimeoutExpired("local", 120),
        ):
            result = import_worker.interpret(job)
            self.assertTrue(result["transient"])
        with patch(
            "pantry.services.import_worker.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "invalid", ""),
        ):
            self.assertFalse(import_worker.interpret(job)["transient"])


class ImportInterfaceTests(ImportFixture, TestCase):
    def test_full_text_review_and_owner_scoped_source(self):
        self.assertEqual(self.client.get(reverse("imports")).status_code, 302)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("imports"), {"action": "text", "text": "have six eggs left"}
        )
        self.assertEqual(response.status_code, 302)
        draft = ImportDraft.objects.get()
        self.assertContains(self.client.get(response.url), "Review observations")
        response = self.client.post(response.url, self.form_data(draft))
        self.assertEqual(response.status_code, 200)
        self.assertIn("preview", response.context)
        self.client.post(
            reverse("confirm"), {"token": response.context["preview"]["token"]}
        )
        self.assertEqual(PantryItem.objects.get().quantity, 6000)
        photo = self.photo()
        source_response = self.client.get(reverse("import_source", args=[photo.pk]))
        self.assertEqual(source_response.status_code, 200)
        self.assertTrue(b"".join(source_response.streaming_content))
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("import_review", args=[photo.pk])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("import_source", args=[photo.pk])).status_code, 404
        )

    def form_data(self, draft):
        return {
            "revision_token": revision_token(draft),
            "action": "preview",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-ingredient": self.eggs.pk,
            "form-0-kind": "set",
            "form-0-amount": "6",
            "form-0-unit": "count",
            "form-0-evidence": "manual",
        }

    def test_expired_source_hidden_multiple_uploads_rejected_and_stale_form(self):
        self.client.force_login(self.owner)
        draft = self.photo()
        ImportJob.objects.filter(draft=draft).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            self.client.get(reverse("import_source", args=[draft.pk])).status_code, 404
        )
        self.assertContains(self.client.get(reverse("imports")), "No worker check")
        self.client.post(
            reverse("imports"),
            {
                "action": "photo",
                "source_type": "fridge",
                "photo": [image_upload(), image_upload()],
            },
        )
        self.assertEqual(ImportDraft.objects.count(), 1)
        draft = imports.create_text(self.owner, "have six eggs left")
        old = self.form_data(draft)
        self.edit(draft)
        self.assertEqual(
            self.client.post(
                reverse("import_review", args=[draft.pk]), old
            ).status_code,
            409,
        )

    def test_invalid_edit_token_is_feedback_not_server_error(self):
        self.client.force_login(self.owner)
        draft = imports.create_text(self.owner, "have six eggs left")
        data = self.form_data(draft)
        data["revision_token"] = signing.dumps(
            {"owner": self.other.pk, "draft": str(draft.pk), "revision": 1}, salt=SALT
        )
        self.assertEqual(
            self.client.post(
                reverse("import_review", args=[draft.pk]), data
            ).status_code,
            422,
        )


class ImportRaces(ImportFixture, TransactionTestCase):
    def race(self, action):
        barrier = Barrier(2)

        def worker(_):
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_one_worker_claim_wins(self):
        self.photo()
        values = self.race(lambda: import_worker.claim())
        self.assertEqual(sum(v is not None for v in values), 1)
        self.assertEqual(ImportJob.objects.get().attempts, 1)

    def test_different_previews_of_same_draft_produce_one_event(self):
        draft = imports.create_text(self.owner, "have six eggs left")
        tokens = [imports.preview(self.owner, draft.pk)["token"] for _ in range(2)]
        iterator = iter(tokens)
        from threading import Lock

        lock = Lock()

        def action():
            with lock:
                token = next(iterator)
            try:
                return inventory.confirm(self.owner, token).pk
            except Conflict:
                return None

        values = self.race(action)
        self.assertEqual(sum(v is not None for v in values), 1)
        self.assertEqual(InventoryEvent.objects.count(), 1)
        draft.refresh_from_db()
        self.assertIsNotNone(draft.applied_event_id)


class WorkerLockTests(ImportFixture, TestCase):
    def test_os_lock_refuses_second_worker_and_releases_on_exit(self):
        from django.core.management.base import CommandError

        from pantry.services.process_lock import import_worker_lock

        with import_worker_lock():
            with self.assertRaises(CommandError):
                with import_worker_lock():
                    pass
        with import_worker_lock():
            pass

    def test_transport_and_application_schemas_match(self):
        from pantry.interpretation.runner import SCHEMA

        self.assertEqual(SCHEMA, candidates.RAW_SCHEMA)


class ModelIdentityTests(ImportFixture, TestCase):
    def trial(self, loaded_digest=None, *, profile="baseline", source_type="fridge"):
        from pantry.interpretation import runner

        job = self.photo().job
        responses = [
            {
                "models": [
                    {
                        "name": runner.MODEL,
                        "digest": runner.EXPECTED_DIGEST,
                        "details": {"runner": "llamacpp"},
                    }
                ]
            },
            {"message": {"content": '{"schema": 1, "items": []}'}},
            {
                "models": [
                    {
                        "name": runner.MODEL,
                        "digest": loaded_digest or runner.EXPECTED_DIGEST,
                        "size_vram": 1234,
                        "runner": "llamacpp",
                    }
                ]
            },
        ]
        with (
            patch.dict(runner.os.environ, {"KITCHER_RECOGNITION_PROFILE": profile}),
            patch.object(runner, "request", side_effect=responses),
            patch.object(runner, "tesseract_path", return_value="local-ocr.exe"),
            patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "visible OCR", ""),
            ),
        ):
            return runner.run(imports.source_path(job.source_name), source_type)

    def test_records_actually_loaded_pinned_manifest(self):
        from pantry.interpretation import runner

        result = self.trial()
        self.assertEqual(result["metadata"]["digest"], runner.EXPECTED_DIGEST)
        self.assertEqual(result["metadata"]["allocated_vram_bytes"], 1234)
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_detail_profile_is_explicitly_recorded_as_a_trial(self):
        result = self.trial(profile="fridge-detail-trial")
        self.assertEqual(result["metadata"]["prompt"], "kitcher.observations.v3-trial")
        self.assertEqual(result["metadata"]["image_views"], 5)
        self.assertEqual(result["metadata"]["ocr"], "eng/psm11")

    def test_detail_profile_preserves_receipt_baseline(self):
        result = self.trial(profile="fridge-detail-trial", source_type="receipt")
        self.assertEqual(result["metadata"]["prompt"], "kitcher.observations.v2")
        self.assertEqual(result["metadata"]["image_views"], 1)
        self.assertEqual(result["metadata"]["ocr"], "eng/psm6")

    def test_backend_selection_cannot_silently_change_recorded_identity(self):
        from pantry.interpretation import runner

        with self.assertRaises(runner.ModelIdentityError):
            self.trial("unverified-manifest")


class CleanupRetryTests(ImportFixture, TestCase):
    def test_locked_source_cannot_turn_committed_cancellation_into_failure(self):
        draft = self.photo()
        name = draft.job.source_name
        with patch.object(
            Path, "unlink", side_effect=PermissionError("Source is being served")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                imports.cancel(self.owner, draft.pk, draft.revision)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "cancelled")
        self.assertTrue(imports.source_path(name).exists())
        import_worker.cleanup()
        self.assertFalse(imports.source_path(name).exists())
