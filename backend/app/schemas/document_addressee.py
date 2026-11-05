"""Frozen addressee of one unit's document on its key date."""
from pydantic import BaseModel


class DocumentAddressee(BaseModel):
    unit_id: str
    unit_number: str
    owner_profile_id: str
    owner_name: str
    key_date: str
