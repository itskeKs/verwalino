"""Future form endpoints can silently accept a filled bot-only field."""
from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse


def honeypot_response(
    fields: Mapping[str, Any], success_content: Any, *, field_name: str = "website",
) -> JSONResponse | None:
    """Return before any side effects when the hidden field is not empty.

    The caller supplies its own normal success body; an empty/missing field
    returns None so the normal handler can continue. Not wired to a route yet.
    """
    if fields.get(field_name, "") != "":
        return JSONResponse(status_code=200, content=success_content)
    return None
