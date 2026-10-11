from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from pantry.services.quantities import DomainError
from pantry.services.recovery import write_export


class Command(BaseCommand):
    help = "Export an owner's portable schema-1 domain data without credentials or import jobs."

    def add_arguments(self, parser):
        parser.add_argument("destination")
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        try:
            owner = get_user_model().objects.get(username=options["username"])
            counts = write_export(owner, options["destination"])
        except (get_user_model().DoesNotExist, OSError, DomainError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Export saved to {options['destination']}; {sum(counts.values())} domain records."
        )
