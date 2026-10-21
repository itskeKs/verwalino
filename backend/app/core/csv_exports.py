"""Pure spreadsheet-safe CSV field formatting shared by financial exports."""
import re


def csv_text(value: str) -> str:
    # Quoting alone does not stop spreadsheet formulas, including whitespace-prefixed ones.
    if value.startswith(('\t', '\r', '\n')) or value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def csv_money(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r'-?(?:0|[1-9][0-9]*)\.[0-9]{2}', value) is None:
        raise ValueError('Report money must be a canonical decimal string')
    return value.replace('.', ',')


