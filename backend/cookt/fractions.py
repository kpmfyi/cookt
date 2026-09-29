from __future__ import annotations

import math
import re
from fractions import Fraction

VULGAR = {
    "¼": Fraction(1, 4),
    "½": Fraction(1, 2),
    "¾": Fraction(3, 4),
    "⅐": Fraction(1, 7),
    "⅑": Fraction(1, 9),
    "⅒": Fraction(1, 10),
    "⅓": Fraction(1, 3),
    "⅔": Fraction(2, 3),
    "⅕": Fraction(1, 5),
    "⅖": Fraction(2, 5),
    "⅗": Fraction(3, 5),
    "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6),
    "⅚": Fraction(5, 6),
    "⅛": Fraction(1, 8),
    "⅜": Fraction(3, 8),
    "⅝": Fraction(5, 8),
    "⅞": Fraction(7, 8),
}
VULGAR_REVERSE = {value: key for key, value in VULGAR.items()}
QUANTITY_PREFIX = re.compile(
    r"^(?P<qty>(?:\d+\s+)?\d+/\d+|(?:\d+)?[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+(?:\.\d+)?)"
)


def parse_quantity(value: str) -> Fraction | None:
    text = value.strip()
    if not text:
        return None
    if text[-1:] in VULGAR:
        whole = text[:-1].strip()
        try:
            return Fraction(int(whole or "0"), 1) + VULGAR[text[-1]]
        except ValueError:
            return None
    if " " in text and "/" in text:
        whole, part = text.split(None, 1)
        try:
            return Fraction(int(whole), 1) + Fraction(part)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def format_quantity(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    whole, remainder = divmod(value.numerator, value.denominator)
    fraction = Fraction(remainder, value.denominator)
    rendered_fraction = VULGAR_REVERSE.get(fraction, f"{fraction.numerator}/{fraction.denominator}")
    return f"{whole}{rendered_fraction}" if whole else rendered_fraction


def scale_quantity(value: str, factor: float) -> str:
    parsed = parse_quantity(value)
    if parsed is None:
        return value
    multiplier = Fraction(str(factor))
    result = (parsed * multiplier).limit_denominator(32)
    return format_quantity(result)


def scale_ingredient_text(text: str, factor: float) -> str:
    match = QUANTITY_PREFIX.match(text.strip())
    if not match or math.isclose(factor, 1):
        return text
    original = match.group("qty")
    replacement = scale_quantity(original, factor)
    start, end = match.span("qty")
    stripped = text.strip()
    return f"{stripped[:start]}{replacement}{stripped[end:]}"


US_UNITS = {
    "cup",
    "cups",
    "tablespoon",
    "tablespoons",
    "tbsp",
    "teaspoon",
    "teaspoons",
    "tsp",
    "ounce",
    "ounces",
    "oz",
    "pound",
    "pounds",
    "lb",
    "lbs",
}
METRIC_UNITS = {
    "g",
    "gram",
    "grams",
    "kg",
    "kilogram",
    "kilograms",
    "ml",
    "milliliter",
    "milliliters",
    "l",
    "liter",
    "liters",
    "°c",
}


def classify_unit(unit: str | None) -> str:
    if not unit:
        return "neutral"
    normalized = unit.strip().casefold().rstrip(".")
    if normalized in US_UNITS or normalized == "°f":
        return "us"
    if normalized in METRIC_UNITS:
        return "metric"
    return "neutral"
