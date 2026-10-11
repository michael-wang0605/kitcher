from django.core.management.base import BaseCommand

from pantry.services.import_worker import cleanup


class Command(BaseCommand):
    help = "Remove overdue private sources and old unreferenced uploads."

    def handle(self, *args, **options):
        self.stdout.write(f"Removed {cleanup()} private source files.")
