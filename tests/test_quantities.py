from decimal import Decimal
from unittest import TestCase

from pantry.services.quantities import DomainError, display, quantity, scale, servings


class QuantityTests(TestCase):
    def test_half_kilogram_and_exact_tenths(self):
        self.assertEqual(quantity("0.5", "kg", "mass"), 500000)
        self.assertEqual(sum(quantity("0.1", "g", "mass") for _ in range(10)), 1000)

    def test_invalid_values_and_dimensions(self):
        for value in [
            "1e1000000",
            "NaN",
            "Infinity",
            "-1",
            "1000000000001",
            "0.00001",
            True,
            0.1,
        ]:
            with self.subTest(value=value), self.assertRaises(DomainError):
                quantity(value, "g", "mass")
        with self.assertRaises(DomainError):
            quantity("1", "us_cup", "mass")

    def test_display_preserves_integer_trailing_zeroes(self):
        for value, expected in [
            (10000, "10"),
            (100000, "100"),
            (1010, "1.01"),
            (0, "0"),
            (None, "Unknown"),
        ]:
            self.assertEqual(display(value), expected)

    def test_serving_validation_and_conservative_scaling(self):
        self.assertEqual(servings("0.001"), Decimal("0.001"))
        self.assertEqual(scale(200000, "3", "2"), 300000)
        self.assertEqual(scale(1000, "1", "3"), 334)
        for value in ["0", "1001", "0.0001", "NaN"]:
            with self.assertRaises(DomainError):
                servings(value)
