import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from PIL import Image

from pantry.models import ImportDraft, ImportJob, InventoryEvent
from pantry.services import import_worker, imports


class BenchmarkTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = override_settings(VAR_DIR=self.root)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.owner = get_user_model().objects.create_user("bench-owner")
        Image.new("RGB", (20, 20), "white").save(self.root / "photo.jpg")
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "results.json"
        self.samples = [
            {
                "path": str(self.root / "photo.jpg"),
                "source_type": "fridge",
                "expected": ["Expected facts independently annotated."],
            }
        ]
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(
            json.dumps({"schema": 1, "samples": self.samples}), encoding="utf-8"
        )

    def run_benchmark(self):
        call_command(
            "benchmark_recognition",
            manifest=str(self.manifest),
            output=str(self.output),
            username=self.owner.username,
            stdout=io.StringIO(),
        )
        return json.loads(self.output.read_text(encoding="utf-8"))

    @staticmethod
    def valid_result():
        return {
            "raw": {
                "schema": 1,
                "items": [
                    {
                        "name": "Unidentified food",
                        "amount": None,
                        "unit": None,
                        "evidence": "A food container.",
                        "uncertain": True,
                    }
                ],
            },
            "metadata": {"prompt": "test-version"},
        }

    def test_retry_latency_records_all_attempts_without_applying_stock(self):
        with (
            patch.object(import_worker, "RETRY_DELAYS", {1: 0, 2: 0}),
            patch.object(
                import_worker,
                "interpret",
                side_effect=[
                    {"error": "temporary local failure", "transient": True},
                    self.valid_result(),
                ],
            ),
        ):
            report = self.run_benchmark()
        sample = report["samples"][0]
        self.assertEqual(sample["job_state"], "succeeded")
        self.assertEqual(sample["status"], "needs_review")
        self.assertEqual(
            [attempt["state"] for attempt in sample["attempts"]],
            ["queued", "succeeded"],
        )
        self.assertEqual(ImportJob.objects.get().attempts, 2)
        self.assertEqual(InventoryEvent.objects.count(), 0)
        self.assertEqual(
            report["inventory_events_before"], report["inventory_events_after"]
        )
        self.assertEqual(report["release_gate"], "unmeasured")
        self.assertEqual(report["summary"]["human_timing"], "unmeasured")
        self.assertIsNone(sample["human_review_seconds"])
        self.assertEqual(len(sample["source_sha256"]), 64)
        self.assertEqual(len(report["manifest_sha256"]), 64)

    def test_terminal_failure_becomes_explicit_manual_review(self):
        with (
            patch.object(import_worker, "RETRY_DELAYS", {1: 0, 2: 0}),
            patch.object(
                import_worker,
                "interpret",
                return_value={"error": "local vision unavailable", "transient": True},
            ),
        ):
            report = self.run_benchmark()
        sample = report["samples"][0]
        self.assertEqual(len(sample["attempts"]), 3)
        self.assertEqual(sample["job_state"], "failed")
        self.assertEqual(sample["status"], "needs_review")
        self.assertTrue(sample["candidates"][0]["unresolved"])
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_all_manifest_references_checked_before_creating_drafts(self):
        self.samples.append(
            {
                "path": str(self.root / "missing.jpg"),
                "source_type": "receipt",
                "expected": ["receipt facts"],
            }
        )
        self.write_manifest()
        with self.assertRaises(CommandError):
            self.run_benchmark()
        self.assertEqual(ImportDraft.objects.count(), 0)

    def test_invalid_source_type_is_a_validation_error(self):
        self.samples[0]["source_type"] = ["fridge"]
        self.write_manifest()
        with self.assertRaises(CommandError):
            self.run_benchmark()
        self.assertEqual(ImportDraft.objects.count(), 0)

    def test_private_output_cannot_replace_manifest(self):
        with self.assertRaises(CommandError):
            call_command(
                "benchmark_recognition",
                manifest=str(self.manifest),
                output=str(self.manifest),
                username=self.owner.username,
                stdout=io.StringIO(),
            )
        self.assertEqual(ImportDraft.objects.count(), 0)
        self.assertEqual(json.loads(self.manifest.read_text())["schema"], 1)

    def test_targeted_claim_does_not_take_another_pending_upload(self):
        data = (self.root / "photo.jpg").read_bytes()
        from django.core.files.uploadedfile import SimpleUploadedFile

        first = imports.create_photo(
            self.owner, SimpleUploadedFile("first.jpg", data), "fridge"
        )
        second = imports.create_photo(
            self.owner, SimpleUploadedFile("second.jpg", data), "fridge"
        )
        claimed = import_worker.claim(draft_id=second.pk)
        self.assertEqual(claimed.draft_id, second.pk)
        self.assertEqual(ImportJob.objects.get(draft=first).state, "queued")


class ResourceSamplerTests(TestCase):
    def test_sampled_window_peak_is_distinct_from_process_lifetime_peak(self):
        import psutil
        import pynvml

        from pantry.benchmarks.resources import ResourceSampler

        state = {"rss": 100, "private": 50, "gpu": 30}

        class FakeProcess:
            pid = 123

            def is_running(self):
                return True

            def children(self, recursive):
                return []

            def memory_info(self):
                return SimpleNamespace(
                    _asdict=lambda: {
                        "rss": state["rss"],
                        "private": state["private"],
                        "peak_wset": 999,
                        "peak_pagefile": 888,
                    }
                )

            def create_time(self):
                return 1000

            def name(self):
                return "benchmark"

            def exe(self):
                return "test-python.exe"

        with (
            patch.object(psutil, "Process", return_value=FakeProcess()),
            patch.object(pynvml, "nvmlInit"),
            patch.object(pynvml, "nvmlShutdown") as shutdown,
            patch.object(pynvml, "nvmlDeviceGetCount", return_value=1),
            patch.object(pynvml, "nvmlDeviceGetHandleByIndex", return_value="gpu"),
            patch.object(pynvml, "nvmlDeviceGetName", return_value="test GPU"),
            patch.object(
                pynvml,
                "nvmlDeviceGetMemoryInfo",
                side_effect=lambda _: SimpleNamespace(used=state["gpu"], total=1000),
            ),
            patch("pantry.benchmarks.resources.threading.Thread") as thread,
        ):
            thread.return_value.is_alive.return_value = False
            with ResourceSampler() as sampler:
                state.update(rss=200, private=70, gpu=60)
                sampler.sample()
            report = sampler.report
        self.assertEqual(report["sampled_peak_working_set_sum_bytes"], 200)
        self.assertEqual(report["sampled_peak_private_commit_sum_bytes"], 70)
        self.assertEqual(report["processes"][0]["lifetime_peak_working_set_bytes"], 999)
        self.assertEqual(report["gpu"][0]["baseline_used_bytes"], 30)
        self.assertEqual(report["gpu"][0]["sampled_peak_used_bytes"], 60)
        self.assertEqual(report["errors"], [])
        shutdown.assert_called_once()

    def test_stale_model_process_record_is_rejected_before_sampling(self):
        import psutil

        from pantry.benchmarks.resources import ResourceSampler

        with TemporaryDirectory() as temp:
            record = Path(temp) / "process.json"
            record.write_text(
                json.dumps(
                    {
                        "pid": 123,
                        "startedTicks": 621355968000000000,
                        "executable": str(Path(temp) / "ollama.exe"),
                    }
                )
            )
            process = SimpleNamespace(create_time=lambda: 1000)
            with (
                patch.object(psutil, "Process", return_value=process),
                self.assertRaises(RuntimeError),
            ):
                ResourceSampler(ollama_record=record)


class ImagePreparationTests(TestCase):
    def test_detail_views_are_bounded_and_do_not_change_the_original(self):
        import base64
        import hashlib

        from pantry.interpretation.runner import prepare_images

        with TemporaryDirectory() as temp:
            source = Path(temp) / "photo.jpg"
            Image.new("RGB", (3000, 4000), "white").save(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            baseline = prepare_images(source)
            detailed = prepare_images(source, detailed=True)
            self.assertEqual(len(baseline), 1)
            self.assertEqual(len(detailed), 5)
            for encoded in detailed:
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as view:
                    self.assertLessEqual(max(view.size), 1280)
                    self.assertEqual(view.format, "JPEG")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
