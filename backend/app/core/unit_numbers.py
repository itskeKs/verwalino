"""Comparison rules for unit numbers without changing their stored spelling."""

import re
import unicodedata


def normalize_unit_number(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()
