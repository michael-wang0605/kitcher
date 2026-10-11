from django.core.management.base import BaseCommand, CommandError

from pantry.services.backups import restore_backup
from pantry.services.quantities import DomainError


class Command(BaseCommand):
    help = "Verify a backup and restore it to a new database file. Never overwrite an existing file."

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("destination")

    def handle(self, *args, **options):
        try:
            result = restore_backup(options["source"], options["destination"])
        except (OSError, DomainError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Verified restore: {result['path']}. Select its separate data directory before running recovery checks."
        )
