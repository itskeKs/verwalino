"""Pure, deliberately conservative pattern filtering for diagnostics.

No person directory is loaded. Capitalized multiword names are a heuristic,
not a guarantee for arbitrary names or unrecognizable personal information.
"""

import re
from uuid import UUID
from urllib.parse import parse_qsl

from app.core.request_id import is_valid_request_id

_EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,}")
# Country/check digits followed by 11–30 alphanumerics, optionally grouped.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_PHONE = re.compile(r"(?<![\w.-])(?:\+|00)?\d[\d ()/-]{5,}\d(?![\w.-])")
_ADDRESS = re.compile(r"\b[\wäöüß-]+(?:straße|strasse|str\.)\s+\d+[a-z]?(?:\s*[-/]\s*\d+[a-z]?)?", re.I)
_NAME = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜ][a-zäöüß]+){1,3}\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_JWT = re.compile(r"(?<![\w.-])\.?[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]{16,}(?![\w.-])")
_HEADER = re.compile(r'''(?im)\b(authorization|cookie|set-cookie)["']?\s*[:=]\s*[^\r\n]+''')
_SECRET = re.compile(r'''(?ix)\b([\w-]*(?:password|passwd|pwd|token|secret|api_key|session)[\w-]*)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;&]+)''')
_URI_AUTH = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s?#@]+@")
_QUERY = re.compile(r"([?&][^\s?&=#]+)=([^&\s#\"'<>]*)")
_SECRET_KEY = re.compile(r"password|passwd|pwd|token|secret|api[_-]?key|session", re.I)


def scrub_text(value: str) -> str:
    """Mask recognizable personal data and credentials, including in URLs."""
    value = _HEADER.sub(lambda m: f"{m[1]}: [redacted:header]", value)
    value = _QUERY.sub(r"\1=[redacted:query]", value)
    value = _URI_AUTH.sub(r"\1[redacted:password]@", value)
    value = _SECRET.sub(lambda m: f"{m[1]}=[redacted:password]" if re.search("password|passwd|pwd", m[1], re.I) else f"{m[1]}=[redacted:token]", value)
    value = _BEARER.sub("[redacted:token]", value)
    value = _JWT.sub("[redacted:token]", value)
    value = _EMAIL.sub("[redacted:email]", value)
    value = _IBAN.sub("[redacted:iban]", value)
    value = _ADDRESS.sub("[redacted:address]", value)
    value = _PHONE.sub("[redacted:phone]", value)
    return _NAME.sub("[redacted:name]", value)


def scrub(value):
    """Copy a diagnostic tree, dropping bodies, identities and frame locals."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in {"user", "vars", "body", "request_body", "cookies", "pre_context", "context_line", "post_context"}:
            continue
        if normalized == "request" and isinstance(item, dict):
            result[key] = scrub({k: v for k, v in item.items() if str(k).lower() not in {"data", "body", "cookies"}})
        elif normalized in {"request_id", "x_request_id"} and is_valid_request_id(item):
            result[key] = item
        elif normalized == "organization_id" and isinstance(item, str):
            try:
                result[key] = str(UUID(item))
            except ValueError:
                result[key] = scrub(item)
        elif normalized in {"authorization", "cookie", "set_cookie", "proxy_authorization"}:
            result[key] = "[redacted:header]"
        elif normalized in {"query_string", "query"}:
            if isinstance(item, str):
                result[key] = "&".join(f"{scrub_text(k)}=[redacted:query]" for k, _ in parse_qsl(item, keep_blank_values=True))
            else:
                result[key] = "[redacted:query]"
        elif _SECRET_KEY.search(normalized):
            result[key] = "[redacted:password]" if re.search("password|passwd|pwd", normalized) else "[redacted:token]"
        elif normalized in {"name", "first_name", "last_name", "full_name", "display_name", "username"}:
            result[key] = "[redacted:name]"
        else:
            result[scrub_text(str(key))] = scrub(item)
    return result
