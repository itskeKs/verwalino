"""Content-free structural inventory; these counts never authorize activation."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, NonNegativeInt


class PreparationCounts(BaseModel):
    properties_total: NonNegativeInt
    properties_without_domain: NonNegativeInt
    properties_without_current_reader: NonNegativeInt
    contacts_total: NonNegativeInt
    contacts_without_domain: NonNegativeInt
    contacts_unknown_creator: NonNegativeInt
    contacts_without_current_reader: NonNegativeInt
    originals_total: NonNegativeInt
    bound_originals_without_review_record: NonNegativeInt
    originals_quarantined: NonNegativeInt
    unbound_originals_unknown_uploader: NonNegativeInt
    bank_accounts_without_scope: NonNegativeInt
    payments_without_scope: NonNegativeInt
    cost_entries_without_scope: NonNegativeInt
    settlements_without_scope: NonNegativeInt
    economic_plans_without_scope: NonNegativeInt
    dunning_without_context: NonNegativeInt
    mandate_originals_without_scope: NonNegativeInt


class AccessPreparationOut(BaseModel):
    generated_at: datetime
    mode: Literal['PREPARATION', 'ENFORCED', 'PAUSED']
    activation_available: Literal[False]
    counts: PreparationCounts
