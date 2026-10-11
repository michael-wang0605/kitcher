"""Versioned observations; interpretation never writes inventory."""

import re
from decimal import ROUND_HALF_UP, Decimal

from pantry.models import Ingredient, IngredientAlias, PackageSize, normalize_name
from pantry.services.quantities import UNITS, DomainError, display, quantity

SCHEMA_VERSION = 1
NUMBER_WORDS = dict(
    zip(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        map(str, range(21)),
        strict=True,
    )
)
NUMBER_WORDS.update({"half": "0.5", "quarter": "0.25"})
UNIT_NAMES = {
    "grams": "g",
    "gram": "g",
    "kilograms": "kg",
    "kilogram": "kg",
    "milliliters": "ml",
    "milliliter": "ml",
    "liters": "l",
    "liter": "l",
    "cups": "us_cup",
    "cup": "us_cup",
    "tbsp": "us_tbsp",
    "tsp": "us_tsp",
    "ounces": "oz",
    "ounce": "oz",
    "pounds": "lb",
    "pound": "lb",
}
RAW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "items"],
    "properties": {
        "schema": {"type": "integer", "const": 1},
        "items": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "amount", "unit", "evidence", "uncertain"],
                "properties": {
                    "name": {"type": "string", "maxLength": 120},
                    "amount": {
                        "type": ["string", "null"],
                        "maxLength": 128,
                        "pattern": r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
                    },
                    "unit": {"type": ["string", "null"], "enum": [*UNITS, None]},
                    "evidence": {"type": "string", "maxLength": 1000},
                    "uncertain": {"type": "boolean"},
                },
            },
        },
    },
}


def lookup(owner):
    result = {
        item.normalized_name: item for item in Ingredient.objects.filter(owner=owner)
    }
    for alias in IngredientAlias.objects.filter(owner=owner).select_related(
        "ingredient"
    ):
        if alias.ingredient.owner_id == owner.pk:
            result[alias.normalized_name] = alias.ingredient
    return result


def observation(name, amount, unit, kind, evidence, catalog, *, uncertain=False):
    item = catalog.get(normalize_name(name))
    issues = []
    if item is None:
        issues.append("Choose a catalog ingredient.")
    if uncertain:
        issues.append("Confirm the uncertain observation.")
    if amount is None or unit is None:
        issues.append("Enter a supported ingredient amount and unit.")
    elif item is not None:
        try:
            quantity(amount, unit, item.dimension, allow_zero=kind != "add")
        except DomainError as exc:
            issues.append(str(exc))
    return {
        "name": name,
        "ingredient": item.pk if item else None,
        "kind": kind,
        "amount": amount or "",
        "unit": unit or "",
        "reconcile": "",
        "evidence": evidence,
        "unresolved": " ".join(issues),
    }


def validate_raw(owner, raw, source_type):
    if source_type not in {"fridge", "receipt"}:
        raise DomainError("Unsupported recognition source.")
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "items"}
        or type(raw["schema"]) is not int
        or raw["schema"] != 1
    ):
        raise DomainError("Recognition returned an unsupported candidate schema.")
    values = raw["items"]
    if not isinstance(values, list) or len(values) > 200:
        raise DomainError("Recognition must return at most 200 observations.")
    catalog = lookup(owner)
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "name",
            "amount",
            "unit",
            "evidence",
            "uncertain",
        }:
            raise DomainError("Recognition returned malformed observation fields.")
        if (
            not isinstance(value["name"], str)
            or not 1 <= len(value["name"].strip()) <= 120
        ):
            raise DomainError("Recognition returned an invalid ingredient name.")
        if (
            not isinstance(value["evidence"], str)
            or len(value["evidence"]) > 1000
            or type(value["uncertain"]) is not bool
        ):
            raise DomainError("Recognition returned invalid evidence.")
        amount, unit = value["amount"], value["unit"]
        if amount is not None and (not isinstance(amount, str) or len(amount) > 128):
            raise DomainError(
                "Recognition amounts must be decimal strings or unresolved."
            )
        if unit is not None and unit not in UNITS:
            raise DomainError("Recognition returned an unsupported unit.")
        # Validate even unknown names: illegal quantities must never masquerade as observations.
        if amount is not None and unit is not None:
            quantity(amount, unit, UNITS[unit][0], allow_zero=source_type == "fridge")
        result.append(
            observation(
                value["name"].strip(),
                amount,
                unit,
                "set" if source_type == "fridge" else "add",
                value["evidence"],
                catalog,
                uncertain=value["uncertain"],
            )
        )
    for row in result:
        row["unresolved"] = (
            row["unresolved"]
            + " Confirm the recognized ingredient amount; a container count does not establish its contents."
        ).strip()
    if not result:
        result.append(
            observation(
                "Unresolved image",
                None,
                None,
                "set" if source_type == "fridge" else "add",
                "No supported observations were extracted. Enter visible facts manually.",
                catalog,
            )
        )
    return result


def parse_text(owner, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
        raise DomainError("Enter between one and 10,000 characters.")
    parts = [part.strip() for part in re.split(r"[;\n]+", text) if part.strip()]
    if len(parts) > 200:
        raise DomainError("Use at most 200 separate statements.")
    catalog = lookup(owner)
    result = []
    for original in parts:
        clause = original.casefold().strip().removeprefix("i ")
        match = re.fullmatch(r"(bought|used|have)\s+(.+)", clause)
        if not match:
            result.append(
                observation(original[:120], None, None, "", original[:1000], catalog)
            )
            continue
        verb, rest = match.groups()
        kind = {"bought": "add", "used": "subtract", "have": "set"}[verb]
        if verb == "have":
            if not rest.endswith(" left"):
                result.append(
                    observation(
                        original[:120], None, None, "", original[:1000], catalog
                    )
                )
                continue
            rest = rest[:-5]
        words = rest.split()
        number = NUMBER_WORDS.get(words[0], words[0])
        remaining = words[1:]
        if not remaining:
            result.append(
                observation(rest[:120], None, None, kind, original[:1000], catalog)
            )
            continue
        unit = UNIT_NAMES.get(remaining[0], remaining[0])
        if unit in UNITS:
            name = " ".join(remaining[1:])
        else:
            name = " ".join(remaining)
            item = catalog.get(normalize_name(name))
            unit = "count" if item and item.dimension == "count" else None
        # Fractions of a package require exactly one matching user-defined package.
        package_match = re.fullmatch(
            r"(bag|bottle|pack|package)\s+(?:of\s+)?(.+)", " ".join(remaining)
        )
        if package_match:
            label, name = package_match.groups()
            item = catalog.get(normalize_name(name))
            packages = list(PackageSize.objects.filter(owner=owner, ingredient=item))
            packages = [p for p in packages if label in normalize_name(p.label).split()]
            try:
                fraction = Decimal(number)
                if not fraction.is_finite() or not Decimal(0) <= fraction <= 1:
                    raise ValueError
                if len(packages) == 1:
                    canonical = int(
                        (Decimal(packages[0].amount) * fraction).to_integral_value(
                            rounding=ROUND_HALF_UP
                        )
                    )
                    number, unit = display(canonical), item.unit
            except (ValueError, ArithmeticError):
                unit = None
        result.append(
            observation(
                name[:120] or original[:120],
                number,
                unit,
                kind,
                original[:1000],
                catalog,
            )
        )
    return result
