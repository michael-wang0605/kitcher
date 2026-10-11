from django.core.management.base import BaseCommand, CommandError

from pantry.services.backups import create_backup
from pantry.services.quantities import DomainError


class Command(BaseCommand):
    help = "Create a verified SQLite backup and configured mirror; retain seven daily copies."

    def add_arguments(self, parser):
        parser.add_argument("--pre-migration", action="store_true")

    def handle(self, *args, **options):
        try:
            status = create_backup(pre_migration=options["pre_migration"])
        except (OSError, DomainError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Verified local backup: {status['path']}")
        if status.get("mirror_error"):
            raise CommandError(
                status["mirror_error"] + " Local backup remains available."
            )
        self.stdout.write(f"Verified mirror: {status['mirror_path']}")
