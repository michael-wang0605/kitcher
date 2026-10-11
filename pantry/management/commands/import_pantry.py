from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from pantry.services.quantities import DomainError
from pantry.services.recovery import import_file


class Command(BaseCommand):
    help = "Validate and atomically import schema-1 domain data into an empty selected account."

    def add_arguments(self, parser):
        parser.add_argument("source")
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            counts = import_file(owner, options["source"])
        except (get_user_model().DoesNotExist, OSError, DomainError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Atomic domain restore complete; {sum(counts.values())} records. Account credentials were not imported."
        )
