"""Real upload/review/publication with the existing fake-provider worker seam."""
import json
import re
import uuid
from pathlib import Path
from playwright.async_api import expect
from pytest_bdd import given, when, then

ROOT = Path(__file__).resolve().parents[4]


@given("the organization enables declaration import with its synthetic AI key")
def declaration_settings(world):
    org = world.act('GET', '/api/organizations/me').json()['id']
    world.data['declaration_org'] = org
    response = world.act('PUT', f'/api/organizations/{org}/ai-settings', json={
        'api_key':'synthetic-journey-key', 'model':'gpt-5.6-luna', 'import_enabled':True,
    })
    assert response.status_code == 200, world.describe_response()


@when("the manager uploads the fictional Lindenallee declaration in the browser")
def upload_declaration(browser_world):
    b = browser_world
    async def run():
        await b.visit('/objekte')
        await b.page.get_by_role('link', name=re.compile('Teilungserklärung')).click()
        await expect(b.page.get_by_label('PDF-Dateien auswählen')).to_be_enabled()
        await b.page.get_by_label('PDF-Dateien auswählen').set_input_files(str(ROOT / 'docs/acceptance/data/teilungserklaerung-lindenallee-12.pdf'))
        async with b.page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/documents')) as uploaded:
            await b.page.get_by_role('button',name='Ausgewählte PDFs hochladen',exact=True).click()
        result = await uploaded.value
        assert result.status == 201, await result.text()
        async with b.page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/start')) as started:
            await b.page.get_by_role('button',name='Import starten',exact=True).click()
        result = await started.value
        assert result.status == 200, await result.text()
        data = await result.json()
        b.world.data['declaration_import'] = data['id']
    b.run(run())


def fictional_provider(document_id):
    from app.declarations.schemas import CoreExtractionOutput, DocumentInventoryOutput, FactPayload, TextValue, IntegerValue, MeaFraction, EvidencePayload, ReconciliationOutput, RuleExtractionOutput, TargetedRecheckOutput
    from app.declarations.fake_provider import FakeDeclarationProvider
    facts = []
    def fact(subject, field, value, quote):
        unit_quotes = {'unit:1':'1 Wohnung / Erdgeschoss 450 / 1.000',
                       'unit:2':'2 Wohnung / Obergeschoss 450 / 1.000',
                       'unit:G1':'G1 Garage / Hof 100 / 1.000'}
        facts.append(FactPayload(subject_key=subject,field=field,raw_value=quote,normalized_value=value,confidence=.99,
            evidence=[EvidencePayload(document_key=document_id,pdf_page=1,quote=unit_quotes.get(subject,quote))]))
    for field,value,quote in [('name','WEG Lindenallee 12','WEG Lindenallee 12'), ('street','Lindenallee 12','Lindenallee 12'),
                              ('zip_code','01234','01234'), ('city','Musterstadt','Musterstadt'), ('management_type','WEG','WEG Lindenallee 12')]:
        fact('property:main',field,TextValue(kind='TEXT',value=value),quote)
    fact('property:main','mea_denominator',IntegerValue(kind='INTEGER',value=1000),'1.000')
    for number,share,kind,location in [('1',450,'WOHNUNG','Erdgeschoss'),('2',450,'WOHNUNG','Obergeschoss'),('G1',100,'STELLPLATZ','Garage / Hof')]:
        subject = f'unit:{number}'
        fact(subject,'number',TextValue(kind='TEXT',value=number),number)
        fact(subject,'usage_type',TextValue(kind='TEXT',value=kind),'Wohnung' if kind=='WOHNUNG' else 'Garage')
        fact(subject,'location',TextValue(kind='TEXT',value=location),location)
        fact(subject,'mea',MeaFraction(kind='MEA',numerator=share,denominator=1000),str(share))
    return FakeDeclarationProvider(
        inventory_output=DocumentInventoryOutput(documents=[{'document_key':document_id,'document_type':'DECLARATION','title':'Fiktive Teilungserklärung'}]),
        core_output=CoreExtractionOutput(facts=facts), rules_output=RuleExtractionOutput(rules=[]),
        reconciliation_output=ReconciliationOutput(facts=facts,rules=[],mea_set_status='COMPLETE'), recheck_output=TargetedRecheckOutput())


@when("the deterministic declaration worker extracts the uploaded scan")
def extract_declaration(world, browser_storage):
    from app.core.storage import LocalFsDocumentStore
    from app.declarations.pipeline import DeclarationPipeline
    from app.workers.declaration_import import DeclarationImportWorker
    import_id = world.data['declaration_import']
    documents = world.act('GET', f'/api/declaration-imports/{import_id}/documents')
    assert documents.status_code == 200, world.describe_response()
    provider = fictional_provider(documents.json()[0]['id'])
    worker = DeclarationImportWorker(DeclarationPipeline(provider, store=LocalFsDocumentStore(browser_storage)))
    world.run(worker.run_once(organization_id=uuid.UUID(world.data['declaration_org'])))
    state = world.act('GET', f'/api/declaration-imports/{import_id}')
    assert state.json()['status']=='NEEDS_REVIEW', state.text


async def _expect_approval_ready(page, import_id):
    prepare = page.get_by_role('button', name='Übernahme vorbereiten', exact=True)
    try:
        await expect(prepare).to_be_enabled()
    except AssertionError as error:
        # This journey uses a fictional scan and the offline provider. Keep the
        # failure note to approval state, never the complete preview or source.
        state = {}
        try:
            response = await page.request.get(f'/api/declaration-imports/{import_id}/approval-preview', timeout=5_000)
            state['preview_status'] = response.status
            if response.status == 200:
                preview = await response.json()
                state.update(
                    draft_revision=preview['draft_revision'],
                    can_approve=preview['can_approve'],
                    pending_count=len(preview['pending_candidate_ids']),
                    blocker_codes=sorted({issue['code'] for issue in preview['issues'] if issue['severity'] == 'BLOCKER'}),
                    issues=sorted([{'code': issue['code'], 'severity': issue['severity']} for issue in preview['issues']],
                                  key=lambda issue: (issue['severity'], issue['code'])),
                )
        except Exception as diagnostic_error:
            state['preview_error'] = type(diagnostic_error).__name__
        try:
            response = await page.request.get(f'/api/declaration-imports/{import_id}/candidates', timeout=5_000)
            state['candidates_status'] = response.status
            if response.status == 200:
                candidates = await response.json()
                state['accepted_critical_evidence_counts'] = sorted(
                    len(candidate['evidence']) for candidate in candidates
                    if candidate['review_status'] in {'ACCEPTED', 'CORRECTED'} and candidate['risk_class'] == 'CRITICAL'
                )
        except Exception as diagnostic_error:
            state['candidates_error'] = type(diagnostic_error).__name__
        try:
            state['visible_pending_count'] = await page.locator('[aria-label="Prüfstatus: PENDING"]:visible').count()
            state['review_errors'] = await page.locator('.declaration-review-page [role="alert"]:visible').all_text_contents()
        except Exception as diagnostic_error:
            state['visible_review_error'] = type(diagnostic_error).__name__
        error.add_note('Declaration approval state: ' + json.dumps(state, ensure_ascii=False, sort_keys=True))
        raise
    return prepare


@when("the manager reviews and confirms the declaration in the browser")
def review_declaration(browser_world):
    b = browser_world
    async def run():
        await expect(b.page.get_by_role('heading',name='Teilungserklärung prüfen',exact=True)).to_be_visible(timeout=20_000)
        pending = b.page.locator('article.declaration-candidate').filter(has=b.page.locator('[aria-label="Prüfstatus: PENDING"]'))
        await expect(pending.first).to_be_visible()
        while await pending.count():
            count = await pending.count()
            await pending.first.get_by_role('button',name='Akzeptieren',exact=True).click()
            await expect(pending).to_have_count(count-1)
        prepare = await _expect_approval_ready(b.page, b.world.data['declaration_import'])
        await prepare.click()
        await b.page.get_by_label('Für mich Lesen, Bearbeiten, Freigeben und Exportieren vorbereiten').check()
        await b.page.get_by_label('Begründung',exact=True).fill('Fiktive Teilungserklärung geprüft und für die Abnahme übernommen.')
        async with b.page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/approve')) as approved:
            await b.page.get_by_role('button',name='Endgültig übernehmen',exact=True).click()
        result=await approved.value
        assert result.status==200, await result.text()
        b.world.data['declaration_property']=(await result.json())['property_id']
        await expect(b.page.get_by_role('heading',name='WEG Lindenallee 12',exact=True)).to_be_visible()
    b.run(run())


@then("the created property contains units 1, 2 and G1 with all 1000 shares")
def declaration_result(browser_world, tmp_path):
    b=browser_world
    response=b.world.act('GET',f"/api/units?property_id={b.world.data['declaration_property']}")
    assert response.status_code==200, b.world.describe_response()
    units=response.json()
    assert {u['number']:int(__import__('decimal').Decimal(u['mea'])) for u in units} == {'1':450,'2':450,'G1':100}
    assert sum(__import__('decimal').Decimal(u['mea']) for u in units)==1000
    async def run():
        for width in (1280,360):
            await b.page.set_viewport_size({'width':width,'height':1000})
            await expect(b.page.get_by_text('G1',exact=True).first).to_be_visible()
            await b.page.screenshot(path=str(tmp_path/f'declaration-property-{width}.png'),full_page=True)
    b.run(run())
