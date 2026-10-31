#!/usr/bin/env node
/* T54: actual UI-only previous-year proposal, never compute/finalize/PDF/send.
 * Parent supplies fresh isolated dev-local UI :13000 / API :18000 under /tmp.
 * No servers, DB, SQL, fixtures, API writes, subprocesses or artifact files.
 * Authored without commands/tests/browser execution; parent owns checks/run.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID, createHash } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T54 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} Vorjahresplan WEG`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, path) => response => response.request().method() === method
  && new URL(response.url()).pathname === path;
function cents(value) {
  assert.equal(typeof value, 'string'); assert.match(value, /^\d+(?:\.\d{1,2})?$/);
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
}
const money = (value, expected) => assert.equal(cents(value), BigInt(expected));
const sorted = rows => {
  assert.ok(Array.isArray(rows), 'Canonical inventory must be a complete array');
  return [...rows].sort((a, b) => a.id.localeCompare(b.id));
};
async function eventually(check) {
  const deadline = Date.now() + 20000;
  for (;;) {
    try { await check(); return; }
    catch (error) {
      if (!(error instanceof assert.AssertionError) || Date.now() >= deadline) throw error;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
}
async function json(response, status = 200) {
  assert.equal(response.status(), status, `${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function selectId(scope, label, id) {
  const control = scope.getByRole('combobox', { name: label, exact: true });
  await eventually(async () => assert.ok((await control.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${label}: live option ${id} required`));
  await control.selectOption(id);
}
async function selectLabel(scope, label, text) {
  const control = field(scope, label);
  await control.getByRole('option', { name: text, exact: true }).waitFor({ state: 'attached' });
  await control.selectOption({ label: text });
}
function receiptPdf(code, amount) {
  const lines = ['T54 SYNTHETIC INVOICE - NOT A REAL CLAIM', `${prefix} ${code}`,
    'Supplier: Synthetic WEG Supplier', 'Invoice date: 2026-12-15',
    'Service period: 2026-01-01 to 2026-12-31', `${code}: gross ${amount} EUR; VAT 0.00 EUR`];
  const stream = `BT /F1 12 Tf 40 780 Td 18 TL\n${lines.map((line, i) =>
    `${i ? 'T* ' : ''}(${line.replace(/([\\()])/g, '\\$1')}) Tj`).join('\n')}\nET\n`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`];
  let pdf = '%PDF-1.4\n'; const offsets = [];
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf);
}
let browser;
(async () => {
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [], allowedWrites = [], previews = [], scopeQueries = [];
    const origins = new Set([base, 'http://127.0.0.1:18000']);
    const sessions = new Set([...origins].map(origin => `${origin}/api/auth/session`));
    let authPhase = 'anonymous', pending = null, previewPermit = null;
    const phases = new WeakMap();
    let page;
    context.on('page', tab => {
      tab.setDefaultTimeout(20000); tab.setDefaultNavigationTimeout(30000);
      if (page && tab !== page) errors.push('Unexpected extra page/popup');
      tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
      tab.on('dialog', async dialog => { errors.push(`Unexpected dialog: ${dialog.message()}`); await dialog.dismiss(); });
      tab.on('download', () => errors.push('Unexpected download'));
      tab.on('console', message => {
        if (message.type() !== 'error') return;
        if (/status of 401/.test(message.text()) && sessions.has(message.location().url) && authPhase === 'anonymous') return;
        errors.push(`console: ${message.text()} ${message.location().url}`);
      });
    });
    context.on('request', request => {
      phases.set(request, authPhase);
      const url = new URL(request.url());
      if (url.pathname === '/api/accounting-scopes/page') scopeQueries.push(url);
    });
    context.on('response', response => {
      if (response.status() < 400) return;
      if (response.status() === 401 && response.request().method() === 'GET'
        && sessions.has(response.url()) && phases.get(response.request()) === 'anonymous') return;
      errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!origins.has(url.origin) || url.protocol !== 'http:') {
        errors.push(`Forbidden resource: ${request.method()} ${request.url()}`); return route.abort('blockedbyclient');
      }
      if (url.pathname.startsWith('/api/reports/') || /\/(?:compute|finalize|pdf|send|export|attest|issue|coverage-attestations)(?:\/|$)/.test(url.pathname)) {
        errors.push(`Forbidden operation: ${request.method()} ${request.url()}`); return route.abort('blockedbyclient');
      }
      if (url.pathname === '/api/economic-plans/previous/preview') {
        previews.push(request);
        const permit = previewPermit;
        try {
          assert.ok(permit && !permit.seen); assert.equal(request.method(), 'POST');
          assert.equal(url.origin, base); assert.equal(url.search, ''); assert.deepEqual(request.postDataJSON(), permit.body);
        } catch (error) { errors.push(`Unpermitted preview: ${error.message}`); return route.abort('blockedbyclient'); }
        permit.seen = true; return route.continue();
      }
      if (request.method() === 'GET') return route.continue();
      const key = `${request.method()} ${url.pathname}`; writes.push(key);
      try {
        assert.ok(pending && !pending.seen); assert.equal(key, pending.key);
        assert.equal(url.origin, base); assert.equal(url.search, '');
        await pending.validate(request);
      } catch (error) { errors.push(`Unpermitted UI write ${key}: ${error.message}`); return route.abort('blockedbyclient'); }
      pending.seen = true; return route.continue();
    });
    assert.equal(typeof context.routeWebSocket, 'function');
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
        && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
        socket.connectToServer(); return;
      }
      errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
    });
    const clean = () => assert.deepEqual(errors, [], 'No unexpected errors, requests, popups or downloads');
    page = await context.newPage();
    async function uiWrite(method, path, action, status, validate) {
      assert.equal(pending, null); assert.equal(previewPermit, null); assert.equal(typeof validate, 'function');
      const permit = { key: `${method} ${path}`, seen: false, validate };
      pending = permit; allowedWrites.push(permit.key);
      try {
        const [response] = await Promise.all([page.waitForResponse(isApi(method, path)), action()]);
        assert.equal(permit.seen, true); const value = await json(response, status); clean(); return value;
      } finally { pending = null; }
    }
    const bodyIs = body => request => assert.deepEqual(request.postDataJSON(), body);
    await page.goto(`${base}/objekte`);
    await field(page, 'E-Mail').fill('test@example.de'); await field(page, 'Passwort').fill('testpassword');
    const principal = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200,
      bodyIs({ email: 'test@example.de', password: 'testpassword' }));
    assert.equal(principal.role, 'OWNER_ADMIN'); authPhase = 'authenticated';
    await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
    await page.goto(`${base}/objekte`); await button(page, '+ Neues Objekt').click();
    await field(page, 'Name *').fill(propertyName); await selectId(page, 'Verwaltungsart *', 'WEG');
    for (const [label, value] of [['Strasse', 'Synthetischer Planweg 54'], ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
    await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
    await field(page, 'Begründung der Freigabe').fill('T54 ausdrücklicher synthetischer Quelldatenzugriff');
    const property = await uiWrite('POST', '/api/properties', () => button(page, 'Speichern').click(), 201, request => {
      const body = request.postDataJSON(); assert.equal(body.name, propertyName); assert.equal(body.management_type, 'WEG');
      assert.equal(body.data_audience.label, propertyName); assert.equal(body.data_audience.grants.length, 1);
      assert.equal(body.data_audience.grants[0].user_id, principal.user_id);
      assert.deepEqual([...body.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
    });
    assert.equal(property.management_type, 'WEG');
    await field(page, 'Objekte suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
    await page.getByRole('link', { name: propertyName, exact: true }).click();
    assert.equal(new URL(page.url()).pathname, `/objekte/${property.id}`);
    // Whole-property WEG scopes need no owners or artificial computed plan.
    async function makeScope(year, capabilities) {
      const label = `${prefix} WEG ${year}`;
      await page.goto(`${base}/berechtigungen`);
      await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
      await selectLabel(page, 'Objekt', propertyName); await field(page, 'Bezeichnung').fill(label);
      await selectLabel(page, 'Verwaltungsart', 'WEG'); await field(page, 'Gültig ab').fill(`${year}-01-01`);
      await field(page, 'Gültig bis (optional)').fill(`${year}-12-31`); await field(page, 'Gesamtes Objekt').check();
      const body = { property_id: property.id, management_mandate_id: null, scope_type: 'WEG', label,
        valid_from: `${year}-01-01`, valid_to: `${year}-12-31`, whole_property: true, units: [] };
      const scope = await uiWrite('POST', '/api/accounting-scopes', () => button(page, 'Bereich verbindlich anlegen').click(), 201, bodyIs(body));
      for (const key of ['property_id', 'scope_type', 'valid_from', 'valid_to', 'whole_property']) assert.equal(scope[key], body[key]);
      await page.getByRole('link', { name: `${label} – Teamrechte verwalten`, exact: true }).click();
      const form = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
      await field(form, 'Teammitglied suchen').fill('test@example.de'); await selectId(form, 'Teammitglied', principal.user_id);
      for (const [cap, caption] of Object.entries({ read: 'Lesen', prepare: 'Vorbereiten', confirm: 'Bestätigen', approve: 'Freigeben', export: 'Exportieren', send: 'Versenden' })) {
        if (cap === 'read') assert.equal(await field(form, caption).isChecked(), true);
        else await field(form, caption).setChecked(capabilities.includes(cap));
      }
      const reason = `T54 ${year}: ausdrückliche ${capabilities.join(', ')} Rechte; kein Export/Versand`;
      await field(form, 'Begründung').fill(reason);
      const grant = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`, () => button(form, 'Freigabe erteilen').click(), 201,
        bodyIs({ user_id: principal.user_id, capabilities: [...capabilities].sort(), reason, expires_at: null }));
      assert.equal(grant.user_id, principal.user_id); assert.deepEqual([...grant.capabilities].sort(), [...capabilities].sort());
      return scope;
    }
    const source = await makeScope(2026, ['read', 'prepare', 'confirm', 'approve']);
    const target = await makeScope(2027, ['read', 'prepare']); assert.notEqual(source.id, target.id);
    // Fixed GET-only inventory allowlist: this client can never submit mutations.
    const readPaths = new Set(['/api/economic-plans', '/api/cost-entries', '/api/invoice-documents',
      '/api/units', '/api/properties', '/api/cost-categories', `/api/properties/${property.id}`,
      `/api/accounting-scopes/${source.id}`, `/api/accounting-scopes/${target.id}`]);
    async function read(path) {
      assert.ok(readPaths.has(path), `Unlisted snapshot GET ${path}`);
      const response = await context.request.get(base + path, { maxRedirects: 0 });
      assert.equal(response.status(), 200, `GET ${path}: ${response.status()} ${await response.text()}`);
      clean(); return response.json();
    }
    const categories = sorted(await read('/api/cost-categories'));
    const category = (code, label) => {
      const matches = categories.filter(value => code ? value.code === code : value.label === label);
      assert.equal(matches.length, 1, `Required canonical actual-cost category ${code || label} unavailable: do not drop reserve oracle`);
      return matches[0];
    };
    const garden = category('GARTENPFLEGE'), manager = category('WEG_VERWALTERKOSTEN'), reserve = category('ERHALTUNGSRUECKLAGE');
    const costs = [], documents = [];
    for (const [cat, amount] of [[garden, '4000.00'], [manager, '3000.00'], [reserve, '6000.00']]) {
      const filename = `${prefix.replace(/ /g, '-')}-${cat.code}.pdf`, bytes = receiptPdf(cat.code, amount);
      await page.goto(`${base}/belege`);
      await field(page, 'Belegdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: bytes });
      const document = await uiWrite('POST', '/api/invoice-documents/upload', () => button(page, 'Hochladen').click(), 201, request => {
        assert.match(request.headers()['content-type'], /^multipart\/form-data; boundary=/);
        const body = request.postDataBuffer(); assert.ok(body.includes(bytes)); assert.ok(body.includes(Buffer.from(filename)));
      });
      assert.equal(document.original_filename, filename);
      assert.equal(document.content_sha256, createHash('sha256').update(bytes).digest('hex'));
      await page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) })
        .getByRole('link', { name: 'Details', exact: true }).click();
      await button(page, 'Kostenposition erstellen').click(); await selectId(page, 'Objekt *', property.id);
      const option = field(page, 'Kategorie *').getByRole('option', { name: `${cat.code} ${cat.label}`, exact: true });
      try { await option.waitFor({ state: 'attached' }); }
      catch (error) { throw new Error(`T54 setup blocked: ${cat.code} is unavailable in the actual invoice cost form. ERHALTUNGSRUECKLAGE must not be dropped or replaced. ${error.message}`); }
      assert.equal(await option.count(), 1, `${cat.code} MUST be available in actual invoice cost UI; reserve may not be skipped`);
      await selectId(page, 'Kategorie *', cat.id);
      for (const [label, value] of [['Rechnungsdatum *', '2026-12-15'], ['Leistungszeitraum von *', '2026-01-01'],
        ['Leistungszeitraum bis *', '2026-12-31'], ['Bruttobetrag *', amount], ['USt-Betrag', '0.00'],
        ['USt-Satz (%)', '0'], ['Belegnummer', `${prefix} ${cat.code}`]]) await field(page, label).fill(value);
      await field(page, 'Abrechnungskreis suchen').fill(source.label); await selectId(page, 'Abrechnungskreis', source.id);
      const costBody = { property_id: property.id, cost_category_id: cat.id,
        cost_category_plan_version: cat.plan_version, vendor_id: null, invoice_date: '2026-12-15',
        service_period_from: '2026-01-01', service_period_to: '2026-12-31', gross_amount: amount,
        vat_amount: '0.00', vat_rate: '0', document_number: `${prefix} ${cat.code}`,
        owner_distribution_key: cat.owner_distribution_key_default ?? null,
        tenant_distribution_key: cat.tenant_distribution_key_default ?? null,
        tenant_apportionable: cat.tenant_apportionability_default, apportionable_tenant_amount: null,
        source: 'PDF_IMPORT', status: 'draft_extracted', accounting_scope_id: source.id,
        source_document_id: document.id };
      const draft = await uiWrite('POST', '/api/cost-entries', () => button(page, 'Entwurf erstellen').click(), 201, bodyIs(costBody));
      await page.waitForURL(`${base}/kosten/${draft.id}`);
      const confirmed = await uiWrite('POST', `/api/cost-entries/${draft.id}/confirm`, () => button(page, 'Bestaetigen').click(), 200,
        request => assert.ok(!request.postData(), 'Confirm has no body'));
      assert.equal(confirmed.status, 'confirmed'); assert.equal(confirmed.cost_category_id, cat.id);
      assert.equal(confirmed.property_id, property.id); // CostEntryOut intentionally omits accounting_scope_id.
      money(confirmed.gross_amount, cents(amount));
      assert.equal(confirmed.service_period_from, '2026-01-01'); assert.equal(confirmed.service_period_to, '2026-12-31');
      readPaths.add(`/api/cost-entries/${confirmed.id}`); readPaths.add(`/api/invoice-documents/${document.id}`);
      costs.push(confirmed);
      await page.goto(`${base}/belege/${document.id}`);
      await page.getByRole('link', { name: 'Verknuepfte Kostenposition', exact: true }).waitFor();
      // The manual release path is complete; the independent OCR worker (T10)
      // is intentionally not started by dev-local. Preserve its actual state
      // in the source snapshot instead of inventing a finished extraction.
      const released = await read(`/api/invoice-documents/${document.id}`);
      assert.equal(released.cost_entry_id, confirmed.id); assert.ok(released.released_at);
      documents.push(released);
    }
    assert.equal(costs.length, 3);
    assert.equal(costs.filter(cost => cost.cost_category_id !== reserve.id).reduce((total, cost) => total + cents(cost.gross_amount), 0n), 700000n);
    assert.equal(costs.filter(cost => cost.cost_category_id === reserve.id).reduce((total, cost) => total + cents(cost.gross_amount), 0n), 600000n);
    async function snapshot() {
      const result = {};
      for (const path of ['/api/economic-plans', '/api/cost-entries', '/api/invoice-documents', '/api/units', '/api/properties', '/api/cost-categories']) {
        result[path] = sorted(await read(path));
      }
      for (const list of ['/api/economic-plans', '/api/cost-entries', '/api/invoice-documents', '/api/units']) {
        for (const value of result[list]) { const path = `${list}/${value.id}`; readPaths.add(path); result[path] = await read(path); }
      }
      for (const path of [`/api/properties/${property.id}`, `/api/accounting-scopes/${source.id}`, `/api/accounting-scopes/${target.id}`]) result[path] = await read(path);
      clean(); return result;
    }
    const before = await snapshot();
    for (const cost of costs) assert.deepEqual(before[`/api/cost-entries/${cost.id}`], cost);
    for (const document of documents) assert.deepEqual(before[`/api/invoice-documents/${document.id}`], document);
    const setupWrites = [...writes];
    const input = { property_id: property.id, plan_year: 2027,
      source_accounting_scope_id: source.id, accounting_scope_id: target.id };
    const panel = () => page.getByRole('region', { name: 'Plan aus Vorjahres-Ist', exact: true });
    const proposalPanel = () => panel().getByRole('region', { name: 'Vorjahresvorschlag', exact: true });
    const scopeGroup = label => panel().getByRole('group', { name: label, exact: true });
    const readGroup = () => scopeGroup('Vorjahres-Ist (Lesekreis)');
    const prepareGroup = () => scopeGroup('Neuer Plan (Vorbereitungskreis)');
    async function chooseScope(group, scope, capability) {
      const start = scopeQueries.length;
      const waiting = page.waitForResponse(response => {
        const url = new URL(response.url());
        return isApi('GET', '/api/accounting-scopes/page')(response)
          && url.searchParams.get('property_id') === property.id
          && url.searchParams.get('scope_type') === 'WEG'
          && url.searchParams.get('capability') === capability
          && url.searchParams.get('q') === scope.label;
      });
      await field(group, 'Abrechnungskreis suchen').fill(scope.label);
      const choices = await json(await waiting);
      assert.ok(choices.items.some(item => item.id === scope.id), `${capability}: explicit live scoped choice required`);
      await selectId(group, 'Abrechnungskreis', scope.id);
      assert.equal(await field(group, 'Abrechnungskreis').inputValue(), scope.id);
      assert.ok(scopeQueries.slice(start).some(url => url.searchParams.get('capability') === capability
        && url.searchParams.get('property_id') === property.id && url.searchParams.get('q') === scope.label));
    }
    await page.goto(`${base}/wirtschaftsplaene`);
    await button(panel(), 'Plan aus Vorjahres-Ist').waitFor();
    assert.equal(await button(panel(), 'Plan aus Vorjahres-Ist').getAttribute('aria-expanded'), 'false');
    assert.equal(await field(panel(), 'Zieljahr').count(), 0, 'Disclosure starts closed');
    assert.equal(previews.length, 0); assert.deepEqual(writes, setupWrites);
    await button(panel(), 'Plan aus Vorjahres-Ist').click();
    await selectId(panel(), 'Objekt für Vorjahresplan', property.id); await field(panel(), 'Zieljahr').fill('2027');
    await chooseScope(readGroup(), source, 'read'); await chooseScope(prepareGroup(), target, 'prepare');
    assert.equal(await field(readGroup(), 'Abrechnungskreis').inputValue(), source.id);
    assert.equal(await field(prepareGroup(), 'Abrechnungskreis').inputValue(), target.id);
    await eventually(async () => assert.equal(await button(panel(), 'Vorjahres-Ist prüfen').isEnabled(), true));
    assert.equal(previews.length, 0, 'No implicit preview while choosing scope/year');
    assert.deepEqual(writes, setupWrites);
    const expectedRows = [
      { category_id: garden.id, category_code: 'GARTENPFLEGE', label: garden.label,
        planned_amount: '4000.00', distribution_key: 'MEA', cost_group: 'UMLAGEFAEHIG' },
      { category_id: manager.id, category_code: 'WEG_VERWALTERKOSTEN', label: manager.label,
        planned_amount: '3000.00', distribution_key: 'MEA', cost_group: 'NICHT_UMLAGEFAEHIG' },
    ];
    const byCode = rows => [...rows].sort((a, b) => a.category_code.localeCompare(b.category_code));
    async function preview() {
      assert.equal(previewPermit, null); assert.equal(pending, null);
      const permit = { body: input, seen: false }; previewPermit = permit;
      let result;
      try {
        const [response] = await Promise.all([page.waitForResponse(isApi('POST', '/api/economic-plans/previous/preview')),
          button(panel(), 'Vorjahres-Ist prüfen').click()]);
        assert.equal(permit.seen, true); result = await json(response);
      } finally { previewPermit = null; }
      assert.equal(result.source_year, 2026); assert.equal(result.plan_year, 2027);
      assert.equal(result.period_start, '2026-01-01'); assert.equal(result.period_end, '2026-12-31');
      assert.match(result.source_digest, /^[a-f0-9]{64}$/);
      assert.equal(typeof result.footnote, 'string'); assert.ok(result.footnote.trim());
      assert.deepEqual(byCode(result.planned_costs), byCode(expectedRows), 'Independent two-cost oracle, not copied preview amounts');
      money(result.reserve_contribution_total, 600000);
      assert.equal(result.planned_costs.some(row => row.category_id === reserve.id), false, 'Reserve is never an operating cost row');
      await proposalPanel().waitFor();
      await eventually(async () => {
        assert.equal(await field(proposalPanel(), `${garden.label} – Jahresbetrag`).inputValue(), '4000.00');
        assert.equal(await field(proposalPanel(), `${manager.label} – Jahresbetrag`).inputValue(), '3000.00');
        assert.equal(await field(proposalPanel(), 'Rücklagenzuführung – Vorschlag').inputValue(), '6000.00');
      });
      assert.equal(await proposalPanel().getByRole('row').count(), 3, 'Header plus exactly two costs; no reserve row');
      for (const cat of [garden, manager]) {
        const row = proposalPanel().getByRole('row').filter({ has: field(page, `${cat.label} – Jahresbetrag`) });
        assert.match(await row.innerText(), new RegExp(cat.code));
        assert.equal(await row.getByRole('cell', { name: 'MEA', exact: true }).count(), 1);
      }
      assert.match(await proposalPanel().innerText(), /Kosten ohne Rücklage: 7000\.00 EUR/);
      assert.match(await proposalPanel().innerText(), /Kosten und Rücklage: 13000\.00 EUR/);
      assert.deepEqual(writes, setupWrites, 'Preview has a separate read permit, never a write');
      assert.equal(JSON.stringify(await snapshot()), JSON.stringify(before), 'Preview changes no source or plan inventory/DTO');
      clean(); return result;
    }
    const first = await preview();
    // Each context edit discards the reviewed proposal and digest synchronously.
    await field(panel(), 'Zieljahr').fill('2028');
    await eventually(async () => assert.equal(await proposalPanel().count(), 0));
    assert.equal(await button(panel(), 'Plan aus Vorjahr anlegen').count(), 0);
    await field(panel(), 'Zieljahr').fill('2027');
    assert.equal(await proposalPanel().count(), 0); assert.equal(previews.length, 1);
    const second = await preview(); assert.equal(second.source_digest, first.source_digest);
    await field(readGroup(), 'Abrechnungskreis').selectOption('');
    await eventually(async () => assert.equal(await proposalPanel().count(), 0));
    assert.equal(await button(panel(), 'Plan aus Vorjahr anlegen').count(), 0);
    await selectId(readGroup(), 'Abrechnungskreis', source.id);
    assert.equal(await proposalPanel().count(), 0); assert.equal(previews.length, 2);
    const reviewed = await preview(); assert.equal(reviewed.source_digest, first.source_digest);
    await field(proposalPanel(), `${garden.label} – Jahresbetrag`).fill('4100.25');
    await field(proposalPanel(), 'Rücklagenzuführung – Vorschlag').fill('6500');
    assert.match(await proposalPanel().innerText(), /Kosten ohne Rücklage: 7100\.25 EUR/);
    assert.match(await proposalPanel().innerText(), /Kosten und Rücklage: 13600\.25 EUR/);
    assert.equal(previews.length, 3, 'Edits do not silently replace the reviewed source');
    assert.deepEqual(writes, setupWrites);
    const adoptedRows = reviewed.planned_costs.map(row => ({ ...row,
      planned_amount: row.category_id === garden.id ? '4100.25' : '3000.00' }));
    const createBody = { ...input, expected_source_digest: reviewed.source_digest,
      planned_costs: adoptedRows, reserve_contribution_total: '6500.00' };
    await button(proposalPanel(), 'Plan aus Vorjahr anlegen').click();
    const dialog = page.getByRole('dialog', { name: 'Plan aus Vorjahr anlegen', exact: true });
    await dialog.waitFor(); assert.match(await dialog.innerText(), /2027/); assert.match(await dialog.innerText(), /13600\.25 EUR/);
    assert.deepEqual(writes, setupWrites, 'Opening confirmation is not creation');
    const created = await uiWrite('POST', '/api/economic-plans/previous',
      () => button(dialog, 'Plan aus Vorjahr anlegen').click(), 201, bodyIs(createBody));
    assert.equal(created.property_id, property.id); assert.equal(created.accounting_scope_id, target.id);
    assert.equal(created.plan_year, 2027); assert.equal(created.period_start, '2027-01-01'); assert.equal(created.period_end, '2027-12-31');
    assert.equal(created.status, 'draft'); assert.deepEqual(created.lines, []); assert.equal(created.snapshot_sha256, null);
    assert.equal(created.finalized_at, null); assert.equal(created.finalized_by, null);
    assert.deepEqual(created.draft_inputs.planned_costs, adoptedRows); money(created.draft_inputs.reserve_contribution_total, 650000);
    money(created.reserve_contribution_total, 650000);
    assert.equal(adoptedRows.reduce((total, row) => total + cents(row.planned_amount), 0n), 710025n);
    assert.equal(adoptedRows.reduce((total, row) => total + cents(row.planned_amount), cents(created.reserve_contribution_total)), 1360025n);
    assert.equal(before['/api/economic-plans'].some(plan => plan.id === created.id), false);
    const detailPath = `/api/economic-plans/${created.id}`; readPaths.add(detailPath);
    await page.waitForURL(`${base}/wirtschaftsplaene/${created.id}`);
    async function assertEditor() {
      await page.getByRole('heading', { name: 'Geplante Kosten', exact: true }).waitFor();
      const editor = page.locator('div.card').filter({ has: page.getByRole('heading', { name: 'Geplante Kosten', exact: true }) });
      await eventually(async () => {
        assert.equal(await field(editor, `${garden.label} – Jahresbetrag`).inputValue(), '4100.25');
        assert.equal(await field(editor, `${manager.label} – Jahresbetrag`).inputValue(), '3000.00');
        money(await field(editor, 'Rücklagenzuführung (Jahr)').inputValue(), 650000);
        assert.equal(await editor.getByRole('combobox').count(), 2);
      });
      for (const [i, row] of adoptedRows.entries()) {
        const control = field(editor, `Kategorie ${i + 1}`);
        assert.equal(await control.inputValue(), row.category_code);
        assert.equal(await control.locator('option:checked').innerText(), `${row.category_code} ${row.label}`);
        const canonical = categories.filter(cat => cat.code === row.category_code);
        assert.equal(canonical.length, 1); assert.equal(canonical[0].id, row.category_id, 'Visible code resolves to persisted canonical category UUID');
        const tr = control.locator('xpath=ancestor::tr');
        assert.equal(await tr.getByRole('cell', { name: 'MEA', exact: true }).count(), 1);
      }
      assert.equal(await editor.getByRole('row').count(), 3);
      assert.equal(await page.getByRole('heading', { name: 'Hausgeld-Vorschüsse je Eigentümer', exact: true }).count(), 0);
      assert.equal(await page.getByRole('heading', { name: 'Geplante Kosten (berechnet)', exact: true }).count(), 0);
      assert.deepEqual(await read(detailPath), created, 'Canonical GET retains editable category UUIDs and amounts'); clean();
    }
    await assertEditor();
    async function reloadDetail() {
      // Only a request begun after this new document committed counts as reload proof.
      let committed = false; const requests = new Set();
      const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
      const started = request => { if (committed) requests.add(request); };
      page.on('framenavigated', navigation); page.on('request', started);
      try {
        const [response] = await Promise.all([page.waitForResponse(response => requests.has(response.request())
          && isApi('GET', detailPath)(response)), page.reload()]);
        assert.deepEqual(await json(response), created);
      } finally { page.off('framenavigated', navigation); page.off('request', started); }
      await assertEditor();
    }
    await reloadDetail();
    await page.setViewportSize({ width: 390, height: 844 }); await reloadDetail();
    // No screenshot/artifact: actual phone controls must remain reachable.
    for (const name of [`${garden.label} – Jahresbetrag`, `${manager.label} – Jahresbetrag`, 'Rücklagenzuführung (Jahr)']) {
      const control = field(page, name); await control.scrollIntoViewIfNeeded(); assert.equal(await control.isVisible(), true);
    }
    const after = await snapshot();
    assert.equal(after['/api/economic-plans'].length, before['/api/economic-plans'].length + 1);
    assert.equal(after['/api/economic-plans'].filter(plan => plan.id === created.id).length, 1);
    assert.deepEqual(after[detailPath], created);
    const withoutNew = { ...after, '/api/economic-plans': after['/api/economic-plans'].filter(plan => plan.id !== created.id) };
    delete withoutNew[detailPath];
    assert.equal(JSON.stringify(withoutNew), JSON.stringify(before), 'All old plans, actual costs, documents, units, categories and source GET DTOs unchanged byte-for-byte JSON after ID sorting');
    for (const cost of costs) assert.deepEqual(after[`/api/cost-entries/${cost.id}`], cost);
    for (const document of documents) assert.deepEqual(after[`/api/invoice-documents/${document.id}`], document);
    assert.deepEqual(writes, [...setupWrites, 'POST /api/economic-plans/previous']);
    assert.deepEqual(writes, allowedWrites, 'Every single-use permit consumed exactly once; no extra POST');
    assert.equal(previews.length, 3); assert.equal(setupWrites.length, 15, 'Login/property/two scopes+grants/three uploads+costs+confirmations only');
    assert.equal(await read(detailPath).then(plan => plan.snapshot_sha256), null); clean();
    console.log('PASS T54: scoped 2026 actuals unchanged; one uncomputed editable 2027 draft; desktop and phone reload persisted.');
  } finally { if (browser) await browser.close(); }
})().catch(error => { console.error(`FAIL T54: ${error.stack || error}`); process.exitCode = 1; });
