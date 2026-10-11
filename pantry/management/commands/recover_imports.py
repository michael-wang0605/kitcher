"""Disable missing-source jobs after a database-only restoration."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from pantry.models import ImportDraft, ImportJob
from pantry.services.imports import source_path
from pantry.services.quantities import DomainError


class Command(BaseCommand):
    help = "Expire jobs whose private source files are missing; keep interpreted review drafts."

    def handle(self, *args, **options):
        missing = 0
        for job in ImportJob.objects.exclude(state__in=["cancelled", "expired"]):
            try:
                available = job.source_name and source_path(job.source_name).is_file()
            except DomainError:
                available = False
            if available:
                continue
            with transaction.atomic():
                locked = ImportJob.objects.select_for_update().get(pk=job.pk)
                ImportJob.objects.filter(pk=locked.pk).update(
                    state="expired",
                    attempt_token=None,
                    lease_until=None,
                    claimed_revision=None,
                    error="Source file was not included in the database backup. Re-upload to recognize again.",
                )
                ImportDraft.objects.filter(
                    pk=locked.draft_id, status="processing"
                ).update(
                    status="expired",
                    revision=F("revision") + 1,
                    updated_at=timezone.now(),
                    error="Source file was not included in the database backup. Re-upload to recognize again.",
                )
            missing += 1
        self.stdout.write(
            f"Expired {missing} missing-source jobs; interpreted review data retained."
        )
