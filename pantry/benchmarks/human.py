"""Private paired human measurements, backed by real inventory confirmations."""

import hashlib
import json
import random
import re
import statistics
import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from pantry.models import ImportDraft, InventoryEvent, normalize_name
from pantry.services.quantities import DomainError

MODES = ("review", "manual")
FIELDS = {
    "ingredient",
    "amount",
    "unit",
    "operation",
    "reconciliation",
    "remove",
    "add",
}


def private_path(value):
    path = Path(value).resolve()
    roots = (Path(settings.VAR_DIR).resolve(), (settings.BASE_DIR / "var").resolve())
    if path.suffix.lower() != ".json" or not any(
        path.is_relative_to(root) for root in roots
    ):
        raise DomainError(
            "Keep human benchmark JSON inside an ignored local data directory."
        )
    return path


def read_json(value):
    path = private_path(value)
    with path.open("rb") as stream:
        encoded = stream.read(8_000_001)
    if len(encoded) > 8_000_000:
        raise DomainError("Human benchmark input exceeds eight million bytes.")
    try:

        def reject_constant(value):
            raise ValueError("Non-finite JSON number: " + value)

        data = json.loads(encoded, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("Human benchmark input must be valid JSON.") from exc
    return path, data, hashlib.sha256(encoded).hexdigest()


def report_samples(report):
    if (
        not isinstance(report, dict)
        or type(report.get("schema")) is not int
        or report["schema"] != 1
    ):
        raise DomainError("Use a schema-1 recognition report.")
    samples = report.get("samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 50:
        raise DomainError("The recognition report needs one to 50 samples.")
    hashes = set()
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("source_type") not in (
            "fridge",
            "receipt",
        ):
            raise DomainError("Each recorded sample must be a fridge or receipt image.")
        digest = sample.get("source_sha256")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or digest in hashes
        ):
            raise DomainError("Each sample needs a distinct recorded source SHA-256.")
        hashes.add(digest)
        try:
            uuid.UUID(sample.get("draft", ""))
        except (ValueError, AttributeError, TypeError) as exc:
            raise DomainError("Each sample needs its original draft UUID.") from exc
        if (
            not isinstance(sample.get("expected"), list)
            or not sample["expected"]
            or any(
                not isinstance(fact, str) or not fact.strip()
                for fact in sample["expected"]
            )
        ):
            raise DomainError("Each sample needs independent expected facts.")
        if (
            not isinstance(sample.get("draft"), str)
            or sample.get("status") != "needs_review"
        ):
            raise DomainError(
                "Prepare from a recognition run that reached review for every sample."
            )
    return samples


def prepare(report_path, report, report_hash):
    samples = report_samples(report)
    randomizer = random.Random(report_hash)
    order = list(range(len(samples)))
    randomizer.shuffle(order)
    counters = {kind: randomizer.choice(MODES) for kind in ("fridge", "receipt")}
    rows = []
    for sample in samples:
        kind = sample["source_type"]
        first = counters[kind]
        counters[kind] = "manual" if first == "review" else "review"
        rows.append(
            {
                "source_sha256": sample["source_sha256"],
                "source_type": kind,
                "source": sample.get("source", ""),
                "expected": sample["expected"],
                "first": first,
                "target_and_baseline_notes": None,
                "expected_facts_checked": False,
                "corrected_fields": None,
                "review": {"seconds": None, "event_ids": None},
                "manual": {"seconds": None, "event_ids": None},
            }
        )
    schedule = []
    for second in (False, True):
        indices = order if not second else list(reversed(order))
        for index in indices:
            mode = rows[index]["first"]
            if second:
                mode = "manual" if mode == "review" else "review"
            schedule.append({"sample": index + 1, "mode": mode})
    return {
        "schema": 1,
        "recognition_report": str(report_path),
        "recognition_sha256": report_hash,
        "operator": None,
        "human_stopwatch_measurements": False,
        "same_catalog_baseline_and_targets": False,
        "protocol": "Start when the assigned photo/draft or manual form is first inspected; stop after the last successful confirmation response. Include reading, corrections, reconciliation, preview, retries, and all manual confirmations. Exclude advance setup/reset and measured automated processing.",
        "schedule": schedule,
        "samples": rows,
        "release_gate": "unmeasured",
    }


def seconds(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,3})?", value
    ):
        raise DomainError(
            "Record positive stopwatch seconds as decimal strings with at most three decimal places."
        )
    number = Decimal(value)
    if not Decimal("0.001") <= number <= Decimal("86400"):
        raise DomainError("Stopwatch seconds must be between 0.001 and 86400.")
    return number


def event_proof(owner, ids, *, mode, sample, used):
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 200
        or any(type(pk) is not int or not 1 <= pk <= 2**63 - 1 for pk in ids)
        or len(set(ids)) != len(ids)
    ):
        raise DomainError("Record one to 200 distinct positive confirmation event IDs.")
    if used.intersection(ids):
        raise DomainError(
            "A confirmation cannot be reused for another method or sample."
        )
    events = list(
        InventoryEvent.objects.filter(owner=owner, pk__in=ids).order_by(
            "created_at", "id"
        )
    )
    if [event.pk for event in events] != ids:
        raise DomainError(
            "Confirmation events must belong to this account and be listed chronologically."
        )
    if (
        InventoryEvent.objects.filter(
            owner=owner,
            created_at__gte=events[0].created_at,
            created_at__lte=events[-1].created_at,
        )
        .exclude(pk__in=ids)
        .exists()
    ):
        raise DomainError(
            "An unrecorded inventory confirmation interrupted this timed method."
        )
    if mode == "review":
        if len(events) != 1 or events[0].source != sample["source_type"]:
            raise DomainError(
                "The review measurement needs its photo draft's single atomic confirmation."
            )
        draft = ImportDraft.objects.filter(
            owner=owner,
            pk=sample["draft"],
            applied_event=events[0],
            status="applied",
            fingerprint=sample["source_sha256"],
            source_type=sample["source_type"],
        ).first()
        if draft is None:
            raise DomainError(
                "Review confirmation does not match the original measured photo draft."
            )
    elif any(event.source != "manual" for event in events):
        raise DomainError(
            "Manual measurements must use explicit manual-form confirmations."
        )
    proof = {}
    expected_kind = "set" if sample["source_type"] == "fridge" else "add"
    for event in events:
        lines = list(event.lines.all()[:201])
        if (
            not 1 <= len(lines) <= 200
            or event.revision_after != event.revision_before + 1
        ):
            raise DomainError(
                "Confirmation has invalid event-line or revision evidence."
            )
        for line in lines:
            snapshot = line.ingredient_snapshot
            key = (
                normalize_name(snapshot["name"]),
                snapshot["dimension"],
                snapshot["unit"],
            )
            if any(
                operation.get("kind") != expected_kind for operation in line.operations
            ):
                raise DomainError(
                    "Compare SET observations for fridge images and ADD purchases for receipts in both methods."
                )
            if key not in proof:
                proof[key] = {
                    "before": line.before,
                    "after": line.after,
                    "reconciliation": line.operations[0].get("reconcile"),
                }
            else:
                if proof[key]["after"] != line.before:
                    raise DomainError(
                        "An untimed inventory change interrupted this measurement."
                    )
                proof[key]["after"] = line.after
    used.update(ids)
    return proof, {
        "event_ids": ids,
        "first_confirmation": events[0].created_at.isoformat(),
        "last_confirmation": events[-1].created_at.isoformat(),
        "confirmation_span_seconds": Decimal(
            str((events[-1].created_at - events[0].created_at).total_seconds())
        ),
        "last_confirmation_at": events[-1].created_at,
    }


def score(owner, sheet, report, report_hash):
    samples = report_samples(report)
    if (
        not isinstance(sheet, dict)
        or type(sheet.get("schema")) is not int
        or sheet["schema"] != 1
        or sheet.get("recognition_sha256") != report_hash
    ):
        raise DomainError(
            "Worksheet must match the unchanged schema-1 recognition report."
        )
    canonical = prepare(sheet.get("recognition_report", ""), report, report_hash)
    rows = sheet.get("samples")
    if (
        not isinstance(rows, list)
        or len(rows) != len(samples)
        or sheet.get("schedule") != canonical["schedule"]
    ):
        raise DomainError(
            "Preserve all paired samples and the prepared counterbalanced schedule."
        )
    completed, missing, used, times, confirmation_times = (
        [],
        [],
        set(),
        {mode: [] for mode in MODES},
        {},
    )
    for index, (row, sample) in enumerate(zip(rows, samples, strict=True)):
        if not isinstance(row, dict) or any(
            row.get(key) != canonical["samples"][index][key]
            for key in ("source_sha256", "source_type", "expected", "first")
        ):
            raise DomainError(
                "Sample identity, expected facts, and assigned method order must stay unchanged."
            )
        modes = [row.get(mode) for mode in MODES]
        if any(not isinstance(value, dict) for value in modes):
            raise DomainError("Keep both review and manual measurements.")
        if (
            any(
                value.get("seconds") is None or value.get("event_ids") is None
                for value in modes
            )
            or row.get("corrected_fields") is None
            or row.get("expected_facts_checked") is not True
            or not isinstance(row.get("target_and_baseline_notes"), str)
            or not row["target_and_baseline_notes"].strip()
        ):
            missing.append(index + 1)
            continue
        if (
            not isinstance(sheet.get("operator"), str)
            or not sheet["operator"].strip()
            or sheet.get("human_stopwatch_measurements") is not True
            or sheet.get("same_catalog_baseline_and_targets") is not True
        ):
            raise DomainError(
                "A human operator must attest to actual stopwatch measurements and equal task conditions."
            )
        corrections = row["corrected_fields"]
        if (
            not isinstance(corrections, list)
            or len(corrections) > 400
            or any(
                not isinstance(change, dict)
                or not isinstance(change.get("field"), str)
                or change["field"] not in FIELDS
                or any(
                    not isinstance(change.get(key), str) or len(change[key]) > 2000
                    for key in ("ingredient", "before", "after", "reason")
                )
                for change in corrections
            )
        ):
            raise DomainError(
                "Record corrected fields as bounded ingredient/field/before/after/reason entries; use [] when none changed."
            )
        proofs, records = {}, {}
        for mode in MODES:
            elapsed = seconds(row[mode]["seconds"])
            proof, evidence = event_proof(
                owner, row[mode]["event_ids"], mode=mode, sample=sample, used=used
            )
            if evidence.pop("confirmation_span_seconds") > elapsed + Decimal("1"):
                raise DomainError(
                    "Stopwatch time cannot be shorter than its recorded confirmation span."
                )
            confirmation_times[(index + 1, mode)] = evidence.pop("last_confirmation_at")
            times[mode].append(elapsed)
            proofs[mode] = proof
            records[mode] = {"seconds": str(elapsed), **evidence}
        if proofs["review"] != proofs["manual"]:
            raise DomainError(
                "Review and manual methods must affect the same ingredients with equal starting/final quantities and reconciliation."
            )
        completed.append(
            {
                "sample": index + 1,
                "source_sha256": sample["source_sha256"],
                "review": records["review"],
                "manual": records["manual"],
                "corrected_fields": corrections,
            }
        )
    scheduled = [
        confirmation_times[(step["sample"], step["mode"])]
        for step in sheet["schedule"]
        if (step["sample"], step["mode"]) in confirmation_times
    ]
    if scheduled != sorted(scheduled):
        raise DomainError(
            "Confirmation order differs from the assigned counterbalanced schedule."
        )
    counts = {
        kind: sum(sample["source_type"] == kind for sample in samples)
        for kind in ("fridge", "receipt")
    }
    full_corpus = len(samples) == 20 and counts == {"fridge": 10, "receipt": 10}
    medians = {
        mode: str(statistics.median(times[mode])) if times[mode] else None
        for mode in MODES
    }
    criterion = "unmeasured"
    if full_corpus and not missing:
        criterion = (
            "passed"
            if Decimal(medians["review"]) <= Decimal(medians["manual"])
            else "failed"
        )
    return {
        "schema": 1,
        "scope": "Human paired timing comparison only; operator timings and expected-fact judgments are attested, not independently measured by this scorer.",
        "recognition_sha256": report_hash,
        "operator": sheet.get("operator"),
        "sample_counts": counts,
        "completed_pairs": len(completed),
        "missing_samples": missing,
        "median_review_seconds": medians["review"],
        "median_manual_seconds": medians["manual"],
        "human_timing_criterion": criterion,
        "release_gate": "unmeasured",
        "measurements": completed,
    }
