"""Bounded, pure pattern filtering for error diagnostics, without a person directory."""

from http import HTTPStatus
import re
from uuid import UUID
from urllib.parse import parse_qsl

from app.core.request_id import is_valid_request_id

# Bound work and disclosure per diagnostic string, before running any patterns.
MAX_SCRUB_CHARS = 8192
REQUEST_HEADER_ALLOWLIST = ("content-type", "content-length", "accept", "user-agent", "x-request-id")

# Token-start anchors prevent retries at every suffix of a hostile long token.
# All token/whitespace repetitions are bounded; repeated groups have disjoint
# separators so they cannot partition the same input in exponentially many ways.
_EMAIL = re.compile(r"(?<![\w.!#$%&'*+/=?^`{|}~-])[\w.!#$%&'*+/=?^`{|}~-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,4}(?![\w.-])")
_IBAN = re.compile(r"(?<!\w)[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}(?!\w)", re.I)
_PHONE = re.compile(r"(?<![\w.+/-])(?:\+\d|\(\d|\d)[\d ()/-]{5,62}\d(?![\w.-])")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ADDRESS = re.compile(r"(?<![\w-])[\w-]{1,64}(?:straße|strasse|str\.)[ \t]{1,8}\d{1,6}[a-z]?(?:[ \t]{0,4}[-/][ \t]{0,4}\d{1,6}[a-z]?)?(?!\w)", re.I)
_TITLE_WORD = r"[A-ZÀ-ÖØ-Þ][a-zßà-öø-ÿ]{1,63}(?:-[A-ZÀ-ÖØ-Þ][a-zßà-öø-ÿ]{1,31})?"
_NAME_WORD = rf"(?:{_TITLE_WORD}|[A-ZÀ-ÖØ-Þ]{{2,64}})"
_NAME = re.compile(rf"\b{_TITLE_WORD}(?:[ \t]{{1,3}}{_TITLE_WORD}){{1,3}}\b")
_COMMA_NAME = re.compile(rf"\b{_NAME_WORD},[ \t]{{1,3}}{_NAME_WORD}\b")
_PARTICLE_NAME = re.compile(rf"\b(?:{_TITLE_WORD}[ \t]{{1,3}})?(?:(?:van|von|de|da|zu)[ \t]{{1,3}}){{1,3}}{_TITLE_WORD}\b")
_OWNER_NAME = re.compile(r"(?i)\b(owner[ \t]{1,8})[^\W\d_][\w'-]{0,63}([ \t]{1,8}not found)\b")
_KEY_VALUE = re.compile(r"\b(Key[ \t]{1,8}\([^()\r\n]{1,128}\)[ \t]{0,8}=[ \t]{0,8})\([^()\r\n]{0,512}\)")
_ERROR_CONTEXT = re.compile(r"(?i)\b(?:exception|traceback)\b")
_INFO_LINE = re.compile(r"(?i)^[ \t]{0,8}INFO\b")
_HTTP_REASON = re.compile(r"\b(?:" + "|".join(re.escape(status.phrase) for status in sorted(HTTPStatus, key=lambda status: -len(status.phrase))) + r")\b", re.I)
_PG_DETAIL = re.compile(r"(?im)^[ \t]{0,32}(?:DETAIL:|HINT:|Failing row contains[ \t]{1,8}\(|Key[ \t]{1,8}\()[^\r\n]{0,8192}(?:\r?\n|$)")
_BEARER = re.compile(r"(?i)\bBearer[ \t]{1,8}[A-Za-z0-9._~+/-]{1,8192}={0,8}")
_JWT = re.compile(r"(?<![\w.-])\.?[A-Za-z0-9_-]{16,4096}\.[A-Za-z0-9_-]{0,4096}\.[A-Za-z0-9_-]{16,4096}(?![\w.-])")
_HEADER = re.compile(r'''(?i)(?<![\w-])(authorization|cookie|set-cookie)["']?[ \t]{0,8}[:=][ \t]{0,8}[^\r\n]{1,8192}''')
_ASSIGNMENT_KEY = re.compile(r'''(?<![\w-])([\w-]{1,64})["']?[ \t]{0,8}[:=][ \t]{0,8}''')
_CREDENTIAL_VALUE = re.compile(r'''"[^"\r\n]{0,8192}(?:"|$)|'[^'\r\n]{0,8192}(?:'|$)|[^\s,;&"']{1,8192}''')
_URI_AUTH = re.compile(r"(?i)(?<![a-z0-9+.-])([a-z][a-z0-9+.-]{0,31}://)[^/\s?#@]{1,512}@")
_QUERY = re.compile(r"([?&][^\s?&=#]{1,128})=([^&\s#\"'<>]{0,8192})")
_SECRET_KEY = re.compile(r"password|passwd|pwd|token|secret|api[_-]?key|session", re.I)
_PASSWORD_KEY = re.compile(r"password|passwd|pwd", re.I)
_TRACKER_ID = re.compile(r"[a-f0-9]{16,32}", re.I)
_TRACKER_ID_LENGTHS = {"event_id": 32, "trace_id": 32, "span_id": 16, "parent_span_id": 16}


def _mask_assignments(value: str) -> str:
    # Scan disjoint spans. Nonsecret keys consume only their prefix, so a
    # traceback prefix or nested assignment cannot hide a following secret.
    parts = []
    start = position = 0
    while match := _ASSIGNMENT_KEY.search(value, position):
        position = match.end()
        if not _SECRET_KEY.search(match[1]):
            continue
        credential = _CREDENTIAL_VALUE.match(value, position)
        if credential is None:
            continue
        kind = "password" if _PASSWORD_KEY.search(match[1]) else "token"
        parts.extend((value[start:match.start()], f"{match[1]}=[redacted:{kind}]"))
        start = position = credential.end()
    parts.append(value[start:])
    return "".join(parts)


def _mask_phone(match: re.Match) -> str:
    number = match[0]
    if _ISO_DATE.fullmatch(number):
        return number
    if number.startswith(("+", "0", "(")) or any(separator in number for separator in " ()/-"):
        return "[redacted:phone]"
    return number


def _mask_names(value: str, *, error_context: bool) -> str:
    value = _COMMA_NAME.sub("[redacted:name]", value)
    value = _PARTICLE_NAME.sub("[redacted:name]", value)
    if not error_context:
        return value
    # Keep HTTP reason phrases intact even next to other capitalized words.
    parts = []
    start = 0
    for match in _HTTP_REASON.finditer(value):
        parts.extend((_NAME.sub("[redacted:name]", value[start:match.start()]), match[0]))
        start = match.end()
    parts.append(_NAME.sub("[redacted:name]", value[start:]))
    return "".join(parts)


def scrub_text(value: str, *, error_context: bool = False) -> str:
    """Mask bounded diagnostic text; generic names need an error context."""
    truncated = len(value) > MAX_SCRUB_CHARS
    value = value[:MAX_SCRUB_CHARS]
    error_context = (error_context or bool(_ERROR_CONTEXT.search(value))) and not _INFO_LINE.match(value)
    value = _PG_DETAIL.sub("", value)
    value = _HEADER.sub(lambda m: f"{m[1]}: [redacted:header]", value)
    value = _QUERY.sub(r"\1=[redacted:query]", value)
    value = _URI_AUTH.sub(r"\1[redacted:password]@", value)
    value = _mask_assignments(value)
    value = _BEARER.sub("[redacted:token]", value)
    value = _JWT.sub("[redacted:token]", value)
    value = _EMAIL.sub("[redacted:email]", value)
    value = _IBAN.sub("[redacted:iban]", value)
    value = _ADDRESS.sub("[redacted:address]", value)
    value = _PHONE.sub(_mask_phone, value)
    value = _KEY_VALUE.sub(r"\1([redacted:name])", value)
    value = _OWNER_NAME.sub(r"\1[redacted:name]\2", value)
    value = _mask_names(value, error_context=error_context)
    return value + ("[truncated]" if truncated else "")


def scrub(value, *, error_context: bool = True):
    """Copy an error event, filtering every container and allowed header value."""
    if isinstance(value, str):
        return scrub_text(value, error_context=error_context)
    if isinstance(value, (list, tuple)):
        return [scrub(item, error_context=error_context) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("level") in ("debug", "info", "warning"):
        error_context = False
    result = {}
    for key, item in value.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in {"user", "vars", "body", "request_body", "cookies", "pre_context", "context_line", "post_context", "server_name"}:
            continue
        if normalized == "request" and isinstance(item, dict):
            result[key] = scrub({k: v for k, v in item.items() if str(k).lower() not in {"data", "body", "cookies"}}, error_context=error_context)
        elif normalized == "headers":
            allowed = {k: v for k, v in item.items() if str(k).lower() in REQUEST_HEADER_ALLOWLIST} if isinstance(item, dict) else {}
            result[key] = scrub(allowed, error_context=error_context)
        elif normalized in {"request_id", "x_request_id"} and is_valid_request_id(item):
            result[key] = item
        elif normalized in _TRACKER_ID_LENGTHS and isinstance(item, str) and len(item) == _TRACKER_ID_LENGTHS[normalized] and _TRACKER_ID.fullmatch(item):
            result[key] = item
        elif normalized == "organization_id" and isinstance(item, str):
            try:
                result[key] = str(UUID(item))
            except ValueError:
                result[key] = scrub(item, error_context=error_context)
        elif normalized in {"authorization", "cookie", "set_cookie", "proxy_authorization"}:
            result[key] = "[redacted:header]"
        elif normalized in {"query_string", "query"}:
            if isinstance(item, str):
                result[key] = "&".join(f"{scrub_text(k)}=[redacted:query]" for k, _ in parse_qsl(item[:MAX_SCRUB_CHARS], keep_blank_values=True))
                if len(item) > MAX_SCRUB_CHARS:
                    result[key] += "[truncated]"
            else:
                result[key] = "[redacted:query]"
        elif _SECRET_KEY.search(normalized):
            result[key] = "[redacted:password]" if _PASSWORD_KEY.search(normalized) else "[redacted:token]"
        elif normalized in {"name", "first_name", "last_name", "full_name", "display_name", "username"}:
            result[key] = "[redacted:name]"
        else:
            result[scrub_text(str(key))] = scrub(item, error_context=error_context or normalized == "exception")
    return result
