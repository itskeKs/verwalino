"""The one IBAN check of the project: SEPA export, invoice proposals and anything else that must not store a wrong IBAN.

A valid checksum does not establish that the account exists. ``mod97`` also serves the creditor identifier check of
``services/sepa_export.py``.
"""
import re

# EPC geographical scope.
SEPA_COUNTRIES = frozenset('AD AL AT BE BG CH CY CZ DE DK EE ES FI FR GB GI GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM VA'.split())


def compact(value):
    if not isinstance(value, str):
        raise ValueError('A bank identifier must be a string')
    return value.replace(' ', '').upper()


def mod97(value):
    remainder = 0
    for character in value:
        digits = character if character.isascii() and character.isdigit() else str(ord(character)-55)
        for digit in digits:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def validate_iban(value) -> str:
    value = compact(value)
    if re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}',value) is None or value[:2] not in SEPA_COUNTRIES:
        raise ValueError('Provide a SEPA-country IBAN of 15–34 characters')
    if value[:2] == 'DE' and (len(value) != 22 or not value[4:].isdigit()):
        raise ValueError('A German IBAN has 22 characters and a numeric BBAN')
    if mod97(value[4:] + value[:4]) != 1:
        raise ValueError('Invalid IBAN checksum')
    return value
