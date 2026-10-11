"""Annotated local trials; no inventory confirmation and no assumed release pass."""

import hashlib
import json
import statistics
import time
from contextlib import nullcontext
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pantry.models import ImportJob, InventoryEvent
from pantry.services import import_worker, imports
from pantry.services.backups import atomic_json
from pantry.services.process_lock import import_worker_lock
from pantry.services.quantities import DomainError


def read_manifest(path):
    with path.open("rb") as stream:
        encoded = stream.read(1000001)
    if len(encoded) > 1000000:
        raise CommandError("Recognition manifest exceeds one million bytes.")
    try:
        manifest = json.loads(encoded)
    except (ValueError, UnicodeError) as exc:
        raise CommandError("Recognition manifest must be valid JSON.") from exc
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema")) is not int
        or manifest["schema"] != 1
    ):
        raise CommandError(
            "Provide an annotated schema=1 sample manifest before running."
        )
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 50:
        raise CommandError("Provide one to 50 annotated samples.")
    # Validate every reference before creating any draft. Actual image validation
    # still uses the application's bounded decoder when each sample is staged.
    for sample in samples:
        if (
            not isinstance(sample, dict)
            or not isinstance(sample.get("source_type"), str)
            or sample["source_type"] not in {"fridge", "receipt"}
        ):
            raise CommandError("Every sample needs a fridge/receipt source type.")
        expected = sample.get("expected")
        if (
            not isinstance(expected, list)
            or not 1 <= len(expected) <= 200
            or any(
                not isinstance(fact, str) or not fact.strip() or len(fact) > 2000
                for fact in expected
            )
        ):
            raise CommandError(
                "Every sample needs independently written expected facts."
            )
        if not isinstance(sample.get("path"), str):
            raise CommandError("Every sample needs a local photo path.")
        source = Path(sample["path"]).resolve()
        if (
            not source.is_file()
            or source.suffix.lower() not in {".jpg", ".jpeg", ".png"}
            or source.stat().st_size > imports.MAX_BYTES
        ):
            raise CommandError(
                "Every sample must reference a supported local photo within the upload limit."
            )
    return manifest, hashlib.sha256(encoded).hexdigest()


class Command(BaseCommand):
    help = "Run a pre-annotated private corpus with actual retries and optional measured memory. Never confirm inventory."

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--username", default="owner")
        parser.add_argument("--measure-memory", action="store_true")
        parser.add_argument(
            "--ollama-record",
            default=str(settings.BASE_DIR / "var" / "ollama-process.json"),
        )

    def handle(self, *args, **options):
        try:
            self.run_trial(options)
        except (OSError, RuntimeError, DomainError) as exc:
            raise CommandError(str(exc)) from exc

    def run_trial(self, options):
        manifest_path = Path(options["manifest"]).resolve()
        manifest, manifest_hash = read_manifest(manifest_path)
        owner = get_user_model().objects.filter(username=options["username"]).first()
        if owner is None:
            raise CommandError("Owner account is missing.")
        output = Path(options["output"]).resolve()
        if output.suffix.lower() != ".json" or not output.is_relative_to(
            Path(settings.VAR_DIR).resolve()
        ):
            raise CommandError(
                "Keep private benchmark JSON inside the configured data directory."
            )
        if output == manifest_path:
            raise CommandError(
                "Benchmark output cannot overwrite its annotation manifest."
            )
        report = {
            "schema": 1,
            "started": timezone.now().isoformat(),
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_hash,
            "annotation_source": manifest.get("annotation_source", ""),
            "samples": [],
            "release_gate": "unmeasured",
        }
        count_before = InventoryEvent.objects.filter(owner=owner).count()
        with import_worker_lock():
            import_worker.cleanup()
            if ImportJob.objects.filter(state__in=["queued", "processing"]).exists():
                raise CommandError(
                    "Finish or cancel pending imports before the benchmark."
                )
            for sample in manifest["samples"]:
                source = Path(sample["path"]).resolve()
                monitor = nullcontext()
                if options["measure_memory"]:
                    from pantry.benchmarks.resources import ResourceSampler

                    monitor = ResourceSampler(ollama_record=options["ollama_record"])
                with monitor as measurement:
                    started = time.monotonic()
                    with source.open("rb") as stream:
                        encoded = stream.read(imports.MAX_BYTES + 1)
                    if len(encoded) > imports.MAX_BYTES:
                        raise CommandError("Source grew beyond its upload limit.")
                    draft = imports.create_photo(
                        owner,
                        SimpleUploadedFile(source.name, encoded),
                        sample["source_type"],
                    )
                    attempts = []
                    while True:
                        if time.monotonic() - started > 430:
                            raise CommandError(
                                "Benchmark did not reach a terminal result within the bounded retry window."
                            )
                        job = import_worker.claim(draft_id=draft.pk)
                        if job is None:
                            current = ImportJob.objects.get(draft=draft)
                            if current.state != "queued":
                                raise CommandError(
                                    "Benchmark job could not be claimed in its current state."
                                )
                            delay = max(
                                0,
                                (
                                    current.next_attempt_at - timezone.now()
                                ).total_seconds(),
                            )
                            time.sleep(min(max(delay, 0.01), 1))
                            continue
                        attempt_started = time.monotonic()
                        result = import_worker.interpret(job)
                        published = import_worker.publish(job, **result)
                        draft.refresh_from_db()
                        job.refresh_from_db()
                        attempts.append(
                            {
                                "attempt": job.attempts,
                                "seconds": round(time.monotonic() - attempt_started, 3),
                                "published": published,
                                "state": job.state,
                                "error": job.error or result.get("error", ""),
                                "metadata": result.get("metadata", {}),
                            }
                        )
                        if job.state != "queued":
                            break
                    seconds = round(time.monotonic() - started, 3)
                record = {
                    "source": str(source),
                    "source_sha256": hashlib.sha256(encoded).hexdigest(),
                    "source_type": sample["source_type"],
                    "expected": sample["expected"],
                    "draft": str(draft.pk),
                    "status": draft.status,
                    "job_state": job.state,
                    "attempts": attempts,
                    "review_seconds": seconds,
                    "candidates": draft.candidates,
                    "raw": result.get("raw"),
                    "error": job.error or result.get("error", ""),
                    "metadata": result.get("metadata", {}),
                    "resources": measurement.report if measurement else None,
                    "human_review_seconds": None,
                    "human_manual_seconds": None,
                    "corrected_fields": None,
                    "quality_review": None,
                }
                report["samples"].append(record)
                report.update(
                    inventory_events_before=count_before,
                    inventory_events_after=InventoryEvent.objects.filter(
                        owner=owner
                    ).count(),
                )
                review = [
                    row["review_seconds"]
                    for row in report["samples"]
                    if row["status"] == "needs_review"
                    and row["job_state"] in {"succeeded", "failed"}
                    and row["attempts"][-1]["published"]
                ]
                report["summary"] = {
                    "sample_count": len(report["samples"]),
                    "reviewable": len(review),
                    "reviewable_within_60_seconds": sum(
                        value <= 60 for value in review
                    ),
                    "median_review_seconds": statistics.median(review)
                    if review
                    else None,
                    "slowest_review_seconds": max(review) if review else None,
                    "human_timing": "unmeasured",
                    "semantic_quality": "unmeasured",
                }
                atomic_json(output, report)
                self.stdout.write(
                    f"{source.name}: {record['status']} in {seconds} seconds, {job.attempts} attempt(s)."
                )
        report["finished"] = timezone.now().isoformat()
        atomic_json(output, report)
        self.stdout.write(
            f"Private results saved to {output}. Human timing, semantic review, and complete corpus acceptance remain required."
        )
