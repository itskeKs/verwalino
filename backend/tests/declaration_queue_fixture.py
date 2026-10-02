"""Create explicit synthetic queue provenance using the production command boundary."""
import uuid
from sqlalchemy import text
from app.models.declaration_import import DeclarationImportDocument
from app.models.property_document import PropertyDocument


async def queue_fixture(session, item, actor, *, needs_document=False):
    await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(actor)})
    if needs_document:
        document = PropertyDocument(organization_id=item.organization_id, doc_type='DECLARATION', title='Synthetic worker source',
            original_filename='synthetic.pdf', mime_type='application/pdf', content_byte_size=1,
            content_sha256='0' * 64, storage_uri=f'synthetic/{uuid.uuid4()}.pdf', storage_backend='TEST', page_count=1, uploaded_by=actor)
        session.add(document)
        await session.flush()
        session.add(DeclarationImportDocument(organization_id=item.organization_id, import_id=item.id,
            document_id=document.id, sequence_no=1, document_role='DECLARATION'))
    await session.flush()
    request = await session.scalar(text("SELECT record_declaration_queue_request(:org,:job,:key,'START')"),
        {'org': item.organization_id, 'job': item.id, 'key': uuid.uuid4()})
    await session.refresh(item)
    return request
