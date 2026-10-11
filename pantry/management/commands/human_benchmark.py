"""Prepare or score private, manually collected human recognition measurements."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from pantry.benchmarks import human
from pantry.services.backups import atomic_json
from pantry.services.quantities import DomainError


class Command(BaseCommand):
    help = "Prepare a paired human timing worksheet or score completed measurements against actual confirmation events. Never writes inventory."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["prepare", "score"])
        parser.add_argument("--report")
        parser.add_argument("--worksheet", required=True)
        parser.add_argument("--output")
        parser.add_argument("--username", default="owner")

    def handle(self, *args, **options):
        try:
            worksheet_path = human.private_path(options["worksheet"])
            if options["action"] == "prepare":
                if not options["report"] or options["output"]:
                    raise DomainError(
                        "Prepare requires --report and writes --worksheet; omit --output."
                    )
                if worksheet_path.exists():
                    raise DomainError(
                        "Use a new worksheet filename; existing human measurements cannot be overwritten."
                    )
                report_path, report, report_hash = human.read_json(options["report"])
                result = human.prepare(report_path, report, report_hash)
                atomic_json(worksheet_path, result)
                self.stdout.write(
                    f"Private worksheet prepared: {worksheet_path}. Timings and release acceptance remain unmeasured."
                )
                return
            if not options["output"] or options["report"]:
                raise DomainError(
                    "Score requires --output and reads the pinned report from --worksheet; omit --report."
                )
            _, sheet, worksheet_hash = human.read_json(worksheet_path)
            if not isinstance(sheet, dict) or not isinstance(
                sheet.get("recognition_report"), str
            ):
                raise DomainError(
                    "Worksheet must reference its original recognition report."
                )
            report_path, report, report_hash = human.read_json(
                sheet["recognition_report"]
            )
            output = human.private_path(options["output"])
            if output in {worksheet_path, report_path} or output.exists():
                raise DomainError(
                    "Use a new score filename; inputs and prior evidence cannot be overwritten."
                )
            owner = (
                get_user_model().objects.filter(username=options["username"]).first()
            )
            if owner is None:
                raise DomainError(
                    "Benchmark owner account is missing in the configured database."
                )
            result = human.score(owner, sheet, report, report_hash)
            result["worksheet_sha256"] = worksheet_hash
            atomic_json(output, result)
            self.stdout.write(
                f"Completed pairs: {result['completed_pairs']}; human timing criterion: {result['human_timing_criterion']}; full release: unmeasured. Private score: {output}."
            )
        except (
            OSError,
            DomainError,
            ValidationError,
            KeyError,
            TypeError,
            OverflowError,
        ) as exc:
            raise CommandError(str(exc)) from exc
