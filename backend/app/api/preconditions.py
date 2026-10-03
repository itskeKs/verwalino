"""Explicit optimistic predecessor shared by immutable review commands."""
import uuid
from fastapi import Header,HTTPException


def expected_revision(if_match:str|None=Header(default=None))->uuid.UUID|None:
    if if_match is None:raise HTTPException(428,'If-Match is required')
    try:
        if not (if_match.startswith('"') and if_match.endswith('"')):raise ValueError()
        return None if if_match=='"none"' else uuid.UUID(if_match[1:-1])
    except ValueError:raise HTTPException(422,'If-Match must be a quoted revision UUID or "none"') from None
