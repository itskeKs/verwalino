"""Publication context must exist before sources are bound, within one transaction."""
import uuid
import pytest
from sqlalchemy import text
from app.repositories.property_knowledge_repo import PropertyKnowledgeRepository
from tests.test_declaration_approval_new_property import fully_reviewed_new_property_import  # noqa: F401
from tests.test_declaration_approval_existing_property import reviewed_existing_property_import  # noqa: F401


async def assert_publication_order(client, fixture, seed, monkeypatch, method, observer):
    item=fixture.item
    original=getattr(PropertyKnowledgeRepository,method)
    observed=[]

    async def inspect_before_sources(repo,plan,approved_by,approved_at,documents):
        row=(await repo._session.execute(text('SELECT status,applied_property_id,approved_by,approval_result_json '
            'FROM declaration_import WHERE id=:id'),{'id':item.id})).one()
        assert row.status=='APPLIED'
        assert row.applied_property_id==plan.property_id and row.approved_by==approved_by
        assert row.approval_result_json['property_id']==str(plan.property_id)
        assert await repo._session.scalar(text('SELECT count(*) FROM property WHERE id=:id'),{'id':plan.property_id})==1
        assert await repo._session.scalar(text('SELECT count(*) FROM property_document WHERE id=ANY(:ids) AND property_id IS NOT NULL'),
            {'ids':list(plan.document_ids)})==0
        # Observe MVCC without the runtime advisory lock that serializes readers
        # behind this open publication transaction; the HTTP request still uses app_rw.
        reader=observer
        assert await reader.scalar(text('SELECT status FROM declaration_import WHERE id=:id'),{'id':item.id})=='NEEDS_REVIEW'
        if method=='apply':
            assert await reader.scalar(text('SELECT count(*) FROM property WHERE id=:id'),{'id':plan.property_id})==0
        observed.append(plan.property_id)
        await original(repo,plan,approved_by,approved_at,documents)

    monkeypatch.setattr(PropertyKnowledgeRepository,method,inspect_before_sources)
    response=await client.post(f'/api/declaration-imports/{item.id}/approve',
        json={'draft_revision':item.draft_revision,'idempotency_key':str(uuid.uuid4())})
    assert response.status_code==200,response.text
    assert observed==[uuid.UUID(response.json()['property_id'])]


@pytest.mark.asyncio
async def test_new_destination_and_receipt_precede_source_binding(client_a_clerk,fully_reviewed_new_property_import,seed_data,monkeypatch,migrator_session):
    await assert_publication_order(client_a_clerk,fully_reviewed_new_property_import,seed_data,monkeypatch,'apply',migrator_session)


@pytest.mark.asyncio
async def test_existing_destination_receipt_precedes_source_binding(client_a_clerk,reviewed_existing_property_import,seed_data,monkeypatch,migrator_session):
    await assert_publication_order(client_a_clerk,reviewed_existing_property_import,seed_data,monkeypatch,'apply_existing',migrator_session)
