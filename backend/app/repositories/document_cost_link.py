"""Published source lookup includes every sealed allocation member.

Only public link columns are referenced; private review identities are not needed
for source display or retained-document obligations.
"""
from sqlalchemy import column,or_,select,table
from sqlalchemy.dialects.postgresql import UUID
from app.models.invoice_document import InvoiceDocument

_links=table('document_cost_link',column('organization_id',UUID(as_uuid=True)),column('source_document_id',UUID(as_uuid=True)),column('cost_entry_id',UUID(as_uuid=True)))


def cost_source_predicate(organization_id,cost_entry_id):
    linked=select(_links.c.source_document_id).where(_links.c.organization_id==organization_id,_links.c.cost_entry_id==cost_entry_id)
    return or_(InvoiceDocument.cost_entry_id==cost_entry_id,InvoiceDocument.id.in_(linked))
