import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import OperationalError, connections
from django.utils import timezone

from pantry.models import WorkerStatus
from pantry.services import import_worker
from pantry.services.process_lock import import_worker_lock


class Command(BaseCommand):
    help = "Run the single local import worker; use --once for one pending job."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        with import_worker_lock():
            self.run_worker(options)

    def run_worker(self, options):
        import_worker.cleanup()
        last_cleanup = time.monotonic()
        try:
            while not (settings.VAR_DIR / "import-worker.stop").exists():
                try:
                    WorkerStatus.objects.update_or_create(
                        name="imports",
                        defaults={
                            "last_seen": timezone.now(),
                            "detail": "Local import worker checked for work.",
                        },
                    )
                    processed = import_worker.process_one()
                    if time.monotonic() - last_cleanup >= 900:
                        import_worker.cleanup()
                        last_cleanup = time.monotonic()
                except OperationalError:
                    connections.close_all()
                    processed = False
                    self.stderr.write("Database busy; the worker will try again.")
                if options["once"]:
                    break
                if not processed:
                    time.sleep(2)
            WorkerStatus.objects.update_or_create(
                name="imports",
                defaults={
                    "last_seen": timezone.now(),
                    "detail": "Import worker stopped; no new claims.",
                },
            )
        except KeyboardInterrupt:
            self.stdout.write(
                "Import worker stopped. Any active lease expires within five minutes."
            )
