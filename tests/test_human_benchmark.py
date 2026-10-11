"""Scorer checks use synthetic fixtures; they are never human release evidence."""

import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from pantry.benchmarks import human
from pantry.models import ImportDraft, InventoryEvent
from pantry.services import catalog, imports, inventory
from pantry.services.quantities import DomainError


class HumanBenchmarkTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = override_settings(VAR_DIR=self.root)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.owner = get_user_model().objects.create_user("human-owner")
        self.other = get_user_model().objects.create_user("other-owner")
        self.eggs = catalog.save_ingredient(self.owner, name="Eggs", dimension="count")

    def report(self, count=4):
        rows = []
        for index in range(count):
            kind = "fridge" if count != 20 or index < 10 else "receipt"
            draft = ImportDraft.objects.create(
                owner=self.owner, source_type=kind, fingerprint=f"{index + 1:064x}"
            )
            rows.append(
                {
                    "source": f"photo-{index}.jpg",
                    "source_sha256": draft.fingerprint,
                    "source_type": kind,
                    "expected": ["Fixture task: six eggs."],
                    "draft": str(draft.pk),
                    "status": "needs_review",
                }
            )
        return {"schema": 1, "samples": rows}

    def worksheet(self, report):
        return human.prepare(self.root / "report.json", report, "a" * 64)

    def apply(self, kind, amount, source="manual"):
        return inventory.confirm(
            self.owner,
            inventory.preview(
                self.owner,
                [inventory.operation(self.eggs, kind, amount)],
                source=source,
            )["token"],
        )

    def complete(
        self, report, *, review_seconds="2", manual_seconds="3", multiple_manual=False
    ):
        sheet = self.worksheet(report)
        sheet.update(
            operator="Synthetic test fixture",
            human_stopwatch_measurements=True,
            same_catalog_baseline_and_targets=True,
        )
        for row in sheet["samples"]:
            row.update(
                target_and_baseline_notes="Fixture: baseline zero; final six eggs.",
                expected_facts_checked=True,
                corrected_fields=[],
            )
        for task in sheet["schedule"]:
            index = task["sample"] - 1
            sample, row, mode = (
                report["samples"][index],
                sheet["samples"][index],
                task["mode"],
            )
            self.apply("set", 0)  # Untimed, explicitly confirmed fixture reset.
            kind = "set" if sample["source_type"] == "fridge" else "add"
            if mode == "review":
                draft = ImportDraft.objects.get(pk=sample["draft"])
                draft = imports.edit(
                    self.owner,
                    draft.pk,
                    draft.revision,
                    [
                        {
                            "ingredient": self.eggs.pk,
                            "kind": kind,
                            "amount": "6",
                            "unit": "count",
                            "reconcile": "",
                            "evidence": "Test-only reviewed quantity.",
                        }
                    ],
                )
                event = inventory.confirm(
                    self.owner, imports.preview(self.owner, draft.pk)["token"]
                )
                row[mode] = {"seconds": review_seconds, "event_ids": [event.pk]}
            else:
                ids = []
                if multiple_manual:
                    ids.append(self.apply(kind, 3000).pk)
                amount = 3000 if multiple_manual and kind == "add" else 6000
                ids.append(self.apply(kind, amount).pk)
                row[mode] = {"seconds": manual_seconds, "event_ids": ids}
        return sheet

    def test_preparation_leaves_real_timings_empty_and_counterbalances_each_type(self):
        report = self.report(20)
        sheet = self.worksheet(report)
        self.assertEqual(len(sheet["schedule"]), 40)
        for kind in ("fridge", "receipt"):
            self.assertEqual(
                sum(
                    row["first"] == "review"
                    for row in sheet["samples"]
                    if row["source_type"] == kind
                ),
                5,
            )
        self.assertIsNone(sheet["operator"])
        self.assertTrue(
            all(row["review"]["seconds"] is None for row in sheet["samples"])
        )
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_empty_worksheet_is_unmeasured(self):
        report = self.report()
        result = human.score(self.owner, self.worksheet(report), report, "a" * 64)
        self.assertEqual(result["completed_pairs"], 0)
        self.assertEqual(result["human_timing_criterion"], "unmeasured")
        self.assertIsNone(result["median_review_seconds"])

    def test_four_completed_pairs_do_not_satisfy_full_corpus(self):
        report = self.report()
        result = human.score(self.owner, self.complete(report), report, "a" * 64)
        self.assertEqual(result["completed_pairs"], 4)
        self.assertEqual(result["median_review_seconds"], "2")
        self.assertEqual(result["human_timing_criterion"], "unmeasured")
        self.assertEqual(result["release_gate"], "unmeasured")

    def test_complete_twenty_fixture_pairs_can_only_pass_human_criterion(self):
        report = self.report(20)
        result = human.score(self.owner, self.complete(report), report, "a" * 64)
        self.assertEqual(result["human_timing_criterion"], "passed")
        self.assertEqual(result["release_gate"], "unmeasured")

    def test_slower_review_fails_the_comparison(self):
        report = self.report(20)
        result = human.score(
            self.owner, self.complete(report, review_seconds="4"), report, "a" * 64
        )
        self.assertEqual(result["human_timing_criterion"], "failed")

    def test_all_manual_confirmations_can_be_recorded(self):
        report = self.report(20)
        result = human.score(
            self.owner, self.complete(report, multiple_manual=True), report, "a" * 64
        )
        self.assertEqual(result["completed_pairs"], 20)
        self.assertEqual(len(result["measurements"][0]["manual"]["event_ids"]), 2)

    def test_confirmation_reuse_and_foreign_owner_are_rejected(self):
        report = self.report(2)
        sheet = self.complete(report)
        with self.assertRaisesMessage(DomainError, "belong to this account"):
            human.score(self.other, sheet, report, "a" * 64)
        sheet["samples"][1]["manual"]["event_ids"] = sheet["samples"][0]["manual"][
            "event_ids"
        ]
        with self.assertRaises(DomainError):
            human.score(self.owner, sheet, report, "a" * 64)

    def test_actual_starting_and_final_quantities_must_match(self):
        report = self.report(1)
        sheet = self.complete(report)
        pk = sheet["samples"][0]["manual"]["event_ids"][0]
        # Deliberately corrupt a test fixture to exercise evidence rejection.
        InventoryEvent.objects.get(pk=pk).lines.update(after=7000)
        with self.assertRaisesMessage(DomainError, "equal starting/final"):
            human.score(self.owner, sheet, report, "a" * 64)

    def test_human_attestation_is_required(self):
        report = self.report(1)
        sheet = self.complete(report)
        sheet["human_stopwatch_measurements"] = False
        with self.assertRaisesMessage(DomainError, "human operator"):
            human.score(self.owner, sheet, report, "a" * 64)

    def test_missing_corrections_stay_unmeasured(self):
        report = self.report(1)
        sheet = self.complete(report)
        sheet["samples"][0]["corrected_fields"] = None
        result = human.score(self.owner, sheet, report, "a" * 64)
        self.assertEqual(result["completed_pairs"], 0)

    def test_invalid_times_duplicate_photos_and_changed_report_are_rejected(self):
        for value in (True, 0, "0", "NaN", "1.0001", "86401", "-1"):
            with self.subTest(value=value), self.assertRaises(DomainError):
                human.seconds(value)
        report = self.report(2)
        duplicate = copy.deepcopy(report)
        duplicate["samples"][1]["source_sha256"] = duplicate["samples"][0][
            "source_sha256"
        ]
        with self.assertRaises(DomainError):
            self.worksheet(duplicate)
        with self.assertRaises(DomainError):
            human.score(self.owner, self.worksheet(report), report, "b" * 64)

    def test_prepare_command_is_private_read_only_and_refuses_overwrite(self):
        report = self.report()
        source = self.root / "report.json"
        source.write_text(json.dumps(report), encoding="utf-8")
        sheet = self.root / "timings.json"
        call_command(
            "human_benchmark",
            "prepare",
            report=str(source),
            worksheet=str(sheet),
            stdout=io.StringIO(),
        )
        self.assertEqual(InventoryEvent.objects.count(), 0)
        with self.assertRaises(CommandError):
            call_command(
                "human_benchmark",
                "prepare",
                report=str(source),
                worksheet=str(sheet),
                stdout=io.StringIO(),
            )
        output = self.root / "score.json"
        call_command(
            "human_benchmark",
            "score",
            worksheet=str(sheet),
            output=str(output),
            username=self.owner.username,
            stdout=io.StringIO(),
        )
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["human_timing_criterion"],
            "unmeasured",
        )
        self.assertEqual(InventoryEvent.objects.count(), 0)

    def test_wrong_source_and_changed_schedule_are_rejected(self):
        report = self.report(1)
        sheet = self.complete(report)
        ImportDraft.objects.filter(pk=report["samples"][0]["draft"]).update(
            fingerprint="f" * 64
        )
        with self.assertRaisesMessage(DomainError, "original measured photo"):
            human.score(self.owner, sheet, report, "a" * 64)
        altered = self.worksheet(report)
        altered["schedule"] = list(reversed(altered["schedule"]))
        with self.assertRaisesMessage(DomainError, "counterbalanced schedule"):
            human.score(self.owner, altered, report, "a" * 64)

    def test_score_command_refuses_to_overwrite_its_inputs(self):
        report = self.report()
        source = self.root / "report.json"
        source.write_text(json.dumps(report), encoding="utf-8")
        sheet = self.root / "timings.json"
        call_command(
            "human_benchmark",
            "prepare",
            report=str(source),
            worksheet=str(sheet),
            stdout=io.StringIO(),
        )
        with self.assertRaises(CommandError):
            call_command(
                "human_benchmark",
                "score",
                worksheet=str(sheet),
                output=str(source),
                username=self.owner.username,
                stdout=io.StringIO(),
            )
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), report)
