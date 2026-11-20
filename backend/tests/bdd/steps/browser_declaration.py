"""Real upload/review/publication with the existing fake-provider worker seam."""
import json
import re
import uuid
from pathlib import Path
from playwright.async_api import expect
from pytest_bdd import when, then

from tests.declaration_fixtures import DeclarationProcessingGate
from tests.offline_declaration_import import fictional_provider, process_queued_imports  # noqa: F401

ROOT = Path(__file__).resolve().parents[4]


# The journey's two choices; the offline worker must find them in the saved settings, not here.
READING_MODEL, EXTRACTION_MODEL = 'gpt-5.4-mini', 'gpt-5.6-luna'
PROVIDER_MODELS = [{'id': model, 'evaluated': False, 'price_known': True} for model in (EXTRACTION_MODEL, READING_MODEL, 'gpt-5.4-nano')]
DESKTOP, PHONE = {'width': 1440, 'height': 1000}, {'width': 360, 'height': 800}


async def _fits_the_width(page):
    assert not await page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth'), 'horizontal scrolling'


@when("the administrator chooses different models for reading documents and extracting information in the settings")
def choose_declaration_models(browser_world):
    b = browser_world
    b.world.data['declaration_org'] = b.world.act('GET', '/api/organizations/me').json()['id']
    async def run():
        async def provider_models(route):
            await route.fulfill(status=200, content_type='application/json', body=json.dumps({'models': PROVIDER_MODELS}))
        # The model list is the provider's answer: there is no provider in this journey.
        await b.page.route('**/ai-settings/models', provider_models)
        await b.visit('/einstellungen')
        await b.page.get_by_label('API-Schlüssel', exact=True).fill('synthetic-journey-key')
        await b.page.get_by_label(re.compile('Wir haben den Anbieter selbst gewählt')).check()
        await b.page.get_by_role('button', name='Modelle laden', exact=True).click()
        await b.select('Dokumente einlesen', READING_MODEL)
        await b.select('Informationen extrahieren', EXTRACTION_MODEL)
        await b.page.get_by_label('Teilungserklärungen importieren').check()
        async with b.page.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/ai-settings')) as saved:
            await b.page.get_by_role('button', name='KI-Einstellungen speichern', exact=True).click()
        assert (await saved.value).status == 200, await (await saved.value).text()
        await expect(b.page.get_by_text('KI-Einstellungen gespeichert.')).to_be_visible()
    b.run(run())


@then("the settings show both choices after a reload")
def declaration_models_after_reload(browser_world):
    b = browser_world
    saved = b.world.act('GET', f"/api/organizations/{b.world.data['declaration_org']}/ai-settings").json()
    assert (saved['document_model'], saved['model']) == (READING_MODEL, EXTRACTION_MODEL)
    async def run():
        for viewport in (DESKTOP, PHONE):
            await b.page.set_viewport_size(viewport)
            await b.page.reload()
            await expect(b.page.get_by_label('Dokumente einlesen', exact=True)).to_have_value(READING_MODEL)
            await expect(b.page.get_by_label('Informationen extrahieren', exact=True)).to_have_value(EXTRACTION_MODEL)
            await _fits_the_width(b.page)
        await b.page.set_viewport_size(DESKTOP)
    b.run(run())


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


@when("the offline worker finishes processing the slow scan on its first attempt")
def extract_declaration(world, browser_storage):
    from sqlalchemy import select
    from app.core.db import get_worker_tenant_session
    from app.core.storage import LocalFsDocumentStore
    from app.models.declaration_import import DeclarationExtractionRun
    import_id = world.data['declaration_import']
    organization_id = uuid.UUID(world.data['declaration_org'])

    async def run():
        gate = DeclarationProcessingGate()
        # The same function the user check runs by hand; the scan has no text layer, so its page is read slowly.
        processed = await process_queued_imports(organization_id, store=LocalFsDocumentStore(browser_storage), gate=gate)
        assert gate.released_in_time, 'the lease was not renewed while the page was being read'
        assert [result.code for result in processed.results] == ['PROCESSED', 'IDLE']
        world.data['offline_run'] = processed
        async with get_worker_tenant_session(organization_id) as session:
            runs = await session.scalars(select(DeclarationExtractionRun.status).where(
                DeclarationExtractionRun.import_id == uuid.UUID(import_id),
            ))
            assert runs.all() == ['SUCCEEDED']

    world.run(run())
    state = world.act('GET', f'/api/declaration-imports/{import_id}')
    assert state.json()['status']=='NEEDS_REVIEW', state.text


@then("page reading used only the chosen reading model and extraction only the chosen extraction model")
def models_used_per_stage(world):
    processed = world.data['offline_run']
    (records,) = processed.imports
    document_id = world.act('GET', f"/api/declaration-imports/{world.data['declaration_import']}/documents").json()[0]['id']
    # The models come from the saved settings, which the browser step saved; the scan has one page.
    assert (records.reading_model, records.extraction_model) == (READING_MODEL, EXTRACTION_MODEL)
    assert [(read.model, read.document_key, read.pdf_page) for read in records.reads] == [(READING_MODEL, document_id, 1)]
    assert {model for _method, model, _pages in records.calls} == {EXTRACTION_MODEL}
    assert {method for method, _model, _pages in records.calls} >= {'inventory', 'extract_core', 'extract_rules', 'reconcile'}
    # Extraction reads the transcript of that page: it is the only page any call names.
    assert {pages for _method, _model, pages in records.calls if pages} == {((document_id, 1),)}
    run = world.act('GET', f"/api/declaration-imports/{world.data['declaration_import']}").json()['runs'][0]
    assert [(stage['stage'], stage['model']) for stage in run['stages']] == [('READING', READING_MODEL), ('EXTRACTION', EXTRACTION_MODEL)]


@then("the import shows the consumption of both stages")
def declaration_cost_view(browser_world):
    b = browser_world
    async def run():
        await expect(b.page.get_by_role('heading', name='Teilungserklärung prüfen', exact=True)).to_be_visible(timeout=30_000)
        view = b.page.get_by_role('region', name='Verbrauch je Importlauf', exact=True)
        for viewport in (DESKTOP, PHONE):
            await b.page.set_viewport_size(viewport)
            for stage, model in (('Dokumente einlesen', READING_MODEL), ('Informationen extrahieren', EXTRACTION_MODEL)):
                block = view.get_by_role('group', name=stage, exact=True)
                await expect(block).to_contain_text(model)
                await expect(block).to_contain_text('Eingabetoken')
                await expect(block).to_contain_text('Geschätzte Kosten')
            await expect(view.get_by_role('group', name='Gesamt', exact=True)).to_contain_text('Laufzeit')
            await _fits_the_width(b.page)
        await b.page.set_viewport_size(DESKTOP)
    b.run(run())


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
