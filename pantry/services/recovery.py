"""Schema-1 owner-domain export and atomic import into an empty local account.

Row IDs are export-local references. Historical snapshot JSON remains archival;
its embedded original IDs are never treated as current foreign keys.
"""

import copy
import json
import math
import re
import uuid
from datetime import datetime
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from pantry import models as domain
from pantry.models import normalize_name
from pantry.services.backups import atomic_json
from pantry.services.quantities import CANONICAL, MAX_QUANTITY, DomainError, servings

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 100000
MAX_INTEGER = 2**63 - 1
# An explicit field allowlist prevents authentication, drafts, and leases entering exports.
TABLES = {
    "ingredients": (domain.Ingredient, "name dimension"),
    "aliases": (domain.IngredientAlias, "ingredient name"),
    "packages": (domain.PackageSize, "ingredient label amount"),
    "items": (domain.PantryItem, "ingredient quantity version last_confirmed"),
    "recipes": (
        domain.Recipe,
        "title favorite default_servings instructions revision archived",
    ),
    "recipe_lines": (
        domain.RecipeIngredient,
        "recipe ingredient amount original_input unresolved position",
    ),
    "selection_entries": (
        domain.SelectionEntry,
        "recipe desired_servings position seen_recipe_revision",
    ),
    "events": (
        domain.InventoryEvent,
        "request_id command_hash source created_at revision_before revision_after source_input recipe_snapshot reversal_of correction_of",
    ),
    "event_lines": (
        domain.InventoryEventLine,
        "event ingredient ingredient_snapshot operations before after version_before version_after delta",
    ),
}
REFERENCES = {
    "ingredient": "ingredients",
    "recipe": "recipes",
    "event": "events",
    "reversal_of": "events",
    "correction_of": "events",
}
ROOT_KEYS = {
    "format",
    "schema_version",
    "exported_at",
    "quantity_scale",
    "pantry_revision",
    "selection_revision",
    "tables",
}


def fail(message):
    raise DomainError("Invalid pantry export: " + message)


def integer(value, *, minimum=0, maximum=MAX_INTEGER, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not int or not minimum <= value <= maximum:
        fail("integer outside the supported range.")


def date_value(value, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64:
        fail("invalid timestamp.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        fail("invalid timestamp.")
    if timezone.is_naive(parsed):
        fail("timestamps must include a timezone.")
    return parsed


def json_value(value, depth=0):
    if depth > 16:
        fail("snapshot is too deeply nested.")
    if value is None or type(value) in {str, bool}:
        return
    if type(value) is int:
        integer(value, minimum=-MAX_INTEGER)
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for part in value:
            json_value(part, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for part in value.values():
            json_value(part, depth + 1)
        return
    fail("invalid JSON snapshot value.")


def dump_domain(owner):
    # The transaction captures one coherent domain state; disk I/O follows commit.
    with transaction.atomic():
        tables = {}
        for name, (model, fields) in TABLES.items():
            if name == "recipe_lines":
                query = model.objects.filter(recipe__owner=owner)
            elif name == "selection_entries":
                query = model.objects.filter(selection__owner=owner)
            elif name == "event_lines":
                query = model.objects.filter(event__owner=owner)
            else:
                query = model.objects.filter(owner=owner)
            rows = []
            for record in query.order_by("pk"):
                row = {"id": record.pk}
                for key in fields.split():
                    field = model._meta.get_field(key)
                    value = getattr(record, field.attname)
                    if isinstance(value, (datetime, uuid.UUID)):
                        value = (
                            value.isoformat()
                            if isinstance(value, datetime)
                            else str(value)
                        )
                    row[key] = value
                # Normalize operation references even after an earlier portable restore.
                if name == "event_lines":
                    row["operations"] = [
                        {**op, "ingredient": row["ingredient"]}
                        for op in row["operations"]
                    ]
                rows.append(row)
            tables[name] = rows
        state = domain.PantryState.objects.filter(owner=owner).first()
        selection = domain.RecipeSelection.objects.filter(owner=owner).first()
        payload = {
            "format": "kitcher.pantry",
            "schema_version": 1,
            "exported_at": timezone.now().isoformat(),
            "quantity_scale": 1000,
            "pantry_revision": state.revision if state else 0,
            "selection_revision": selection.revision if selection else 0,
            "tables": tables,
        }
    validate_export(payload)
    return payload


def write_export(owner, path):
    payload = dump_domain(owner)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise DomainError("Export exceeds the supported 32 MiB recovery limit.")
    atomic_json(Path(path), payload)
    return {name: len(rows) for name, rows in payload["tables"].items()}


def read_export(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        fail("file exceeds 32 MiB.")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                fail("duplicate JSON object key.")
            result[key] = value
        return result

    try:
        data = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda value: fail("non-finite JSON number."),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DomainError("Invalid pantry export JSON.") from exc
    validate_export(data)
    return data


def validate_export(payload):
    if not isinstance(payload, dict) or set(payload) != ROOT_KEYS:
        fail("unexpected root fields.")
    if (
        payload["format"] != "kitcher.pantry"
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
    ):
        fail("unsupported schema version.")
    if type(payload["quantity_scale"]) is not int or payload["quantity_scale"] != 1000:
        fail("unsupported quantity scale.")
    date_value(payload["exported_at"])
    integer(payload["pantry_revision"])
    integer(payload["selection_revision"])
    tables = payload["tables"]
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        fail("unexpected tables.")
    lookup = {}
    total = 0
    for name, (model, fields) in TABLES.items():
        rows = tables[name]
        if not isinstance(rows, list):
            fail("tables must be arrays.")
        total += len(rows)
        if total > MAX_ROWS:
            fail("too many records.")
        expected = {"id", *fields.split()}
        lookup[name] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected:
                fail(f"unexpected fields in {name}.")
            integer(row["id"], minimum=1)
            if row["id"] in lookup[name]:
                fail("duplicate export ID.")
            lookup[name][row["id"]] = row
            for key in fields.split():
                value = row[key]
                field = model._meta.get_field(key)
                if value is None and field.null:
                    continue
                if isinstance(field, models.ForeignKey):
                    integer(value, minimum=1)
                elif isinstance(field, models.BooleanField):
                    if type(value) is not bool:
                        fail("boolean field must be true or false.")
                elif isinstance(field, models.IntegerField):
                    integer(
                        value,
                        minimum=-MAX_QUANTITY if key == "delta" else 0,
                        maximum=MAX_QUANTITY
                        if key in {"amount", "quantity", "before", "after", "delta"}
                        else MAX_INTEGER,
                    )
                elif isinstance(field, models.DateTimeField):
                    date_value(value)
                elif isinstance(field, models.UUIDField):
                    if not isinstance(value, str):
                        fail("request ID must be a UUID string.")
                    try:
                        if str(uuid.UUID(value)) != value:
                            fail("request ID must be canonical.")
                    except ValueError:
                        fail("invalid request UUID.")
                elif isinstance(field, models.JSONField):
                    json_value(value)
                    if not isinstance(value, list if key == "operations" else dict):
                        fail("unexpected snapshot structure.")
                    if len(json.dumps(value, ensure_ascii=False)) > 1000000:
                        fail("snapshot exceeds one million characters.")
                elif isinstance(field, (models.CharField, models.TextField)):
                    limit = field.max_length or 100000
                    if (
                        not isinstance(value, str)
                        or len(value) > limit
                        or (not field.blank and not value.strip())
                    ):
                        fail("invalid text field.")
                    if field.choices and value not in dict(field.choices):
                        fail("invalid choice.")
                else:
                    fail("unsupported field.")
                if not isinstance(field, (models.ForeignKey, models.JSONField)):
                    try:
                        field.run_validators(
                            date_value(value)
                            if isinstance(field, models.DateTimeField)
                            else value
                        )
                    except ValidationError:
                        fail("field outside its supported range.")
    for name, rows in tables.items():
        for row in rows:
            for key, target in REFERENCES.items():
                if (
                    key in row
                    and row[key] is not None
                    and row[key] not in lookup[target]
                ):
                    fail("missing export reference.")
    names = set()
    for row in tables["ingredients"]:
        normalized = normalize_name(row["name"])
        if not normalized or normalized in names:
            fail("duplicate normalized ingredient name.")
        names.add(normalized)
    ingredient_names = {
        normalize_name(row["name"]): row["id"] for row in tables["ingredients"]
    }
    names = set()
    for row in tables["aliases"]:
        normalized = normalize_name(row["name"])
        if not normalized or normalized in names:
            fail("duplicate normalized alias.")
        names.add(normalized)
        if (
            normalized in ingredient_names
            and ingredient_names[normalized] != row["ingredient"]
        ):
            fail("alias conflicts with an ingredient name.")
    unique(tables["packages"], ("ingredient", "label"))
    unique(tables["items"], ("ingredient",))
    unique(tables["selection_entries"], ("recipe",))
    unique(tables["selection_entries"], ("position",))
    unique(tables["event_lines"], ("event", "ingredient"))
    unique(tables["events"], ("request_id",))
    for row in tables["recipe_lines"] + tables["selection_entries"]:
        integer(row["position"], maximum=2**31 - 1)
    for row in tables["packages"] + tables["recipe_lines"]:
        if row["amount"] is not None:
            integer(row["amount"], minimum=1, maximum=MAX_QUANTITY)
    for row in tables["recipes"]:
        integer(row["default_servings"], minimum=1, maximum=1000000)
        integer(row["revision"], minimum=1)
    for row in tables["selection_entries"]:
        integer(row["desired_servings"], minimum=1, maximum=1000000)
        integer(
            row["seen_recipe_revision"],
            minimum=1,
            maximum=lookup["recipes"][row["recipe"]]["revision"],
        )
    if (
        len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        > MAX_BYTES
    ):
        fail("domain exceeds 32 MiB.")
    validate_history(payload, lookup)
    return lookup


def unique(rows, keys):
    values = [tuple(row[key] for key in keys) for row in rows]
    if len(values) != len(set(values)):
        fail("duplicate domain key.")


def validate_history(payload, lookup):
    tables = payload["tables"]
    events = sorted(tables["events"], key=lambda row: row["revision_before"])
    if payload["pantry_revision"] != len(events):
        fail("pantry revision differs from event history.")
    lines_by_event = {}
    for line in tables["event_lines"]:
        lines_by_event.setdefault(line["event"], []).append(line)
    balances, versions, seen, reversals = {}, {}, set(), set()
    for revision, event in enumerate(events):
        if (
            event["revision_before"] != revision
            or event["revision_after"] != revision + 1
        ):
            fail("event revision sequence is inconsistent.")
        if not re.fullmatch(r"[0-9a-f]{64}", event["command_hash"]):
            fail("invalid historical command hash.")
        if (event["source"] == "reversal") != (event["reversal_of"] is not None):
            fail("reversal source and link disagree.")
        if (event["source"] == "correction") != (event["correction_of"] is not None):
            fail("correction source and link disagree.")
        if event["source"] == "cooking" or event["recipe_snapshot"]:
            validate_recipe_snapshot(event["recipe_snapshot"])
        if event["reversal_of"] and event["correction_of"]:
            fail("an event cannot be both a correction and reversal.")
        for key in ("reversal_of", "correction_of"):
            if event[key] is not None and event[key] not in seen:
                fail("history link must reference an earlier event.")
        if event["reversal_of"]:
            if event["reversal_of"] in reversals:
                fail("duplicate reversal.")
            reversals.add(event["reversal_of"])
        lines = lines_by_event.get(event["id"], [])
        if (
            not 1 <= len(lines) <= 200
            or sum(len(line["operations"]) for line in lines) > 200
        ):
            fail("event must contain one to 200 operations.")
        for key in ("reversal_of", "correction_of"):
            if event[key] is not None:
                original = {
                    line["ingredient"]: line
                    for line in lines_by_event.get(event[key], [])
                }
                if set(original) != {line["ingredient"] for line in lines}:
                    fail("linked history has different ingredients.")
                if key == "reversal_of" and any(
                    original[line["ingredient"]]["delta"] is None
                    or line["delta"] != -original[line["ingredient"]]["delta"]
                    for line in lines
                ):
                    fail("reversal does not invert the original effective quantities.")
        for line in lines:
            ingredient = line["ingredient"]
            if line["before"] != balances.get(ingredient) or line[
                "version_before"
            ] != versions.get(ingredient, 0):
                fail("history balance or version chain is inconsistent.")
            if line["version_after"] != line["version_before"] + 1:
                fail("event line version does not advance once.")
            calculated_delta = (
                line["after"] - line["before"]
                if line["after"] is not None and line["before"] is not None
                else None
            )
            if line["delta"] != calculated_delta:
                fail("event delta differs from its before/after quantities.")
            snapshot = line["ingredient_snapshot"]
            if (
                set(snapshot) != {"name", "unit", "dimension"}
                or snapshot["dimension"]
                != lookup["ingredients"][ingredient]["dimension"]
                or snapshot["unit"] != CANONICAL[snapshot["dimension"]]
            ):
                fail("invalid ingredient snapshot dimension.")
            if (
                not isinstance(snapshot["name"], str)
                or not snapshot["name"].strip()
                or len(snapshot["name"]) > 120
            ):
                fail("invalid ingredient snapshot name.")
            replay_operations(line)
            balances[ingredient], versions[ingredient] = (
                line["after"],
                line["version_after"],
            )
        seen.add(event["id"])
    items = {row["ingredient"]: row for row in tables["items"]}
    if set(items) != set(balances):
        fail("pantry items differ from the recorded history.")
    for ingredient, item in items.items():
        if (
            item["quantity"] != balances[ingredient]
            or item["version"] != versions[ingredient]
        ):
            fail("current stock differs from the final history state.")
        if item["last_confirmed"] is None:
            fail("confirmed stock must have a timestamp.")


def validate_recipe_snapshot(snapshot):
    expected = {
        "id",
        "revision",
        "title",
        "default_servings",
        "chosen_servings",
        "instructions",
        "ingredients",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != expected:
        fail("invalid recipe snapshot fields.")
    integer(snapshot["id"], minimum=1)
    integer(snapshot["revision"], minimum=1)
    for key, limit in (("title", 160), ("instructions", 100000)):
        if (
            not isinstance(snapshot[key], str)
            or len(snapshot[key]) > limit
            or (key == "title" and not snapshot[key].strip())
        ):
            fail("invalid recipe snapshot text.")
    for key in ("default_servings", "chosen_servings"):
        if not isinstance(snapshot[key], str):
            fail("snapshot servings must be decimal strings.")
        servings(snapshot[key])
    rows = snapshot["ingredients"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        fail("invalid recipe snapshot ingredient count.")
    references = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "ingredient",
            "name",
            "unit",
            "required",
            "actual",
        }:
            fail("invalid recipe snapshot ingredient.")
        integer(row["ingredient"], minimum=1)
        if row["ingredient"] in references:
            fail("duplicate snapshot ingredient.")
        references.add(row["ingredient"])
        if (
            not isinstance(row["name"], str)
            or not row["name"].strip()
            or len(row["name"]) > 120
            or not isinstance(row["unit"], str)
            or row["unit"] not in {"g", "ml", "count"}
        ):
            fail("invalid snapshot ingredient name or unit.")
        integer(row["required"], minimum=1, maximum=MAX_QUANTITY)
        integer(row["actual"], maximum=MAX_QUANTITY)


def replay_operations(line):
    operations = line["operations"]
    if not 1 <= len(operations) <= 200:
        fail("invalid operation count.")
    current = line["before"]
    for op in operations:
        if not isinstance(op, dict) or set(op) != {
            "ingredient",
            "kind",
            "amount",
            "reconcile",
        }:
            fail("invalid operation fields.")
        integer(op["ingredient"], minimum=1)
        if op["ingredient"] != line["ingredient"] or op["kind"] not in {
            "set",
            "add",
            "subtract",
        }:
            fail("invalid operation ingredient or kind.")
        integer(op["amount"], maximum=MAX_QUANTITY, nullable=op["kind"] == "set")
        integer(op["reconcile"], maximum=MAX_QUANTITY, nullable=True)
        if op["kind"] == "set":
            current = op["amount"]
        else:
            if op["kind"] == "add" and op["amount"] == 0:
                fail("addition must be positive.")
            if op["reconcile"] is not None:
                current = op["reconcile"]
            if current is None:
                if op["kind"] == "subtract" and op["amount"] == 0:
                    continue
                fail("unknown quantity requires reconciliation.")
            current += op["amount"] if op["kind"] == "add" else -op["amount"]
            integer(current, maximum=MAX_QUANTITY)
    if current != line["after"]:
        fail("operations do not produce the recorded balance.")


def ensure_empty(owner):
    for model in (
        domain.Ingredient,
        domain.IngredientAlias,
        domain.PackageSize,
        domain.PantryItem,
        domain.InventoryEvent,
        domain.Recipe,
        domain.ImportDraft,
        domain.ImportJob,
    ):
        if model.objects.filter(owner=owner).exists():
            raise DomainError(
                "Portable restore requires an empty local account; existing data was not changed."
            )
    state = domain.PantryState.objects.filter(owner=owner).first()
    selection = domain.RecipeSelection.objects.filter(owner=owner).first()
    if (state and state.revision) or (
        selection and (selection.revision or selection.entries.exists())
    ):
        raise DomainError("Portable restore requires an empty account state.")


def load_domain(owner, payload):
    # Freeze caller-owned objects; all validation precedes the first database write.
    payload = copy.deepcopy(payload)
    validate_export(payload)
    tables = payload["tables"]
    mapped = {name: {} for name in TABLES}
    with transaction.atomic():
        ensure_empty(owner)
        selection, _ = domain.RecipeSelection.objects.get_or_create(owner=owner)
        for name, (model, fields) in TABLES.items():
            rows = tables[name]
            if name == "events":
                rows = sorted(rows, key=lambda row: row["revision_before"])
            for row in rows:
                values = {}
                for key in fields.split():
                    value = row[key]
                    field = model._meta.get_field(key)
                    if key in REFERENCES:
                        value = (
                            mapped[REFERENCES[key]][value].pk
                            if value is not None
                            else None
                        )
                    elif isinstance(field, models.DateTimeField):
                        value = date_value(value, nullable=field.null)
                    elif key == "operations":
                        value = [
                            {
                                **op,
                                "ingredient": mapped["ingredients"][
                                    op["ingredient"]
                                ].pk,
                            }
                            for op in value
                        ]
                    values[field.attname] = value
                if "owner" in {field.name for field in model._meta.fields}:
                    values["owner"] = owner
                if name == "selection_entries":
                    values["selection"] = selection
                record = model(**values)
                # Immutable historical records are inserted once. Only the automatic
                # creation timestamp needs a restore-specific update before commit.
                record.full_clean(
                    exclude=[
                        field.name
                        for field in model._meta.fields
                        if isinstance(field, models.JSONField)
                        or (field.null and getattr(record, field.attname) is None)
                    ]
                )
                record.save(force_insert=True)
                if name == "events":
                    model.objects.filter(pk=record.pk).update(
                        created_at=values["created_at"]
                    )
                mapped[name][row["id"]] = record
        domain.PantryState.objects.update_or_create(
            owner=owner, defaults={"revision": payload["pantry_revision"]}
        )
        domain.RecipeSelection.objects.filter(pk=selection.pk).update(
            revision=payload["selection_revision"]
        )
    return {name: len(rows) for name, rows in tables.items()}


def import_file(owner, path):
    try:
        return load_domain(owner, read_export(path))
    except ValidationError as exc:
        raise DomainError(
            "Export failed domain validation; restore was rolled back."
        ) from exc
