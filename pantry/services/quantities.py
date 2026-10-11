"""Exact quantities at the SQLite/PostgreSQL boundary."""

from decimal import (
    ROUND_CEILING,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)

MAX_QUANTITY = 10**15
UNITS = {
    "g": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "oz": ("mass", Decimal("28.349523125")),
    "lb": ("mass", Decimal("453.59237")),
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
    "us_cup": ("volume", Decimal("236.5882365")),
    "us_tbsp": ("volume", Decimal("14.78676478125")),
    "us_tsp": ("volume", Decimal("4.92892159375")),
    "count": ("count", Decimal("1")),
}
CANONICAL = {"mass": "g", "volume": "ml", "count": "count"}


class DomainError(ValueError):
    """A user-correctable validation problem."""


class Busy(DomainError):
    """A transient database lock; retry the identical confirmation."""


class Conflict(DomainError):
    """The reviewed state changed before confirmation."""


def decimal(value):
    if isinstance(value, (float, bool)):
        raise DomainError("Use a decimal number, not a floating-point value.")
    if len(str(value)) > 128:
        raise DomainError("The number is too long.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainError("Enter a valid number.") from exc
    if not result.is_finite():
        raise DomainError("The amount must be finite.")
    return result


def quantity(value, unit, dimension, *, allow_zero=True):
    if unit not in UNITS or UNITS[unit][0] != dimension:
        raise DomainError("Choose a unit compatible with this ingredient.")
    number = decimal(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise DomainError(
            "Enter a positive amount."
            if not allow_zero
            else "Amounts cannot be negative."
        )
    with localcontext() as context:
        context.prec = 28
        if number > Decimal(MAX_QUANTITY) / (UNITS[unit][1] * 1000):
            raise DomainError("Amount exceeds the supported limit.")
        raw = number * UNITS[unit][1] * 1000
        if raw > MAX_QUANTITY:
            raise DomainError("Amount exceeds the supported limit.")
        amount = int(raw.to_integral_value(rounding=ROUND_HALF_UP))
    if number > 0 and amount == 0:
        raise DomainError("Amount is too small; use at least 0.001 canonical units.")
    return amount


def validate_stored(value, *, nullable=False):
    if nullable and value is None:
        return value
    if type(value) is not int or not 0 <= value <= MAX_QUANTITY:
        raise DomainError("Invalid canonical quantity.")
    return value


def display(value):
    if value is None:
        return "Unknown"
    text = format(Decimal(value) / 1000, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def servings(value):
    number = decimal(value)
    if (
        not Decimal("0.001") <= number <= 1000
        or number * 1000 != (number * 1000).to_integral_value()
    ):
        raise DomainError(
            "Servings must be 0.001-1000 with at most three decimal places."
        )
    return number


def scale(amount, desired, default):
    with localcontext() as context:
        context.prec = 28
        result = (
            Decimal(amount) * servings(desired) / servings(default)
        ).to_integral_value(rounding=ROUND_CEILING)
    return validate_stored(int(result))
