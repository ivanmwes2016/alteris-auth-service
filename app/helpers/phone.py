"""
Phone numbers typed into forms. Loose about formatting (spaces, dashes, brackets, a
leading +), strict about it being a number at all: 7 to 15 digits, the international
maximum.
"""

import re

_ALLOWED = re.compile(r"\+?[\d\s\-().]+")
MIN_DIGITS, MAX_DIGITS = 7, 15


def clean_phone(value: object) -> str | None:
    """The number with its spacing tidied, or None if it isn't a plausible phone number."""
    if not isinstance(value, str):
        return None
    number = " ".join(value.split())
    if not _ALLOWED.fullmatch(number):
        return None
    digits = sum(char.isdigit() for char in number)
    return number if MIN_DIGITS <= digits <= MAX_DIGITS else None
