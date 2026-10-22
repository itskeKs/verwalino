#!/usr/bin/env node
/* T53: UI-only synthetic batch dunning; DRAFTS ONLY, never issue/send/PDF.
 * Parent supplies the isolated app :13000 / API :18000 after DB tests are green.
 * Starts no server/DB, uses no SQL, fixtures, request clients or output files.
 * Every write requires a single-use UI permit; preview has its own exact-body
 * read permit. Only loopback HTTP resources and local Vite HMR are admitted.
 * Authored without running commands/tests/browser; parent owns syntax/run checks.
 * Batch selectors follow the final components/DunningBatch.tsx labels.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T53 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} Mahnlauf MIET`, scopeName = `${prefix} Mietkreis`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
const sorted = rows => {
  assert.ok(Array.isArray(rows), 'Canonical list GET must return an array');
  return [...rows].sort((a, b) => a.id.localeCompare(b.id));
};
function money(value, expected) {
  assert.equal(typeof value, 'string');
  assert.match(value, /^\d+(?:\.\d{1,2})?$/);
  const [whole, fraction = ''] = value.split('.');
  assert.equal(BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0')), BigInt(expected));
}
async function eventually(check) {
  const end = Date.now() + 20000;
  for (;;) {
    try { await check(); return; }
    catch (error) {
      if (!(error instanceof assert.AssertionError) || Date.now() >= end) throw error;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
}
async function successfulJson(response, status = 200) {
  assert.equal(response.status(), status,
    `${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function selectLabel(scope, name, label) {
  const select = scope.getByRole('combobox', { name, exact: true });
  await select.getByRole('option', { name: label, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label });
}
async function selectId(scope, name, id) {
  const select = scope.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${name}: missing observed ID ${id}`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
}
// Reject responses from requests begun before the replacement document committed.
async function freshReload(page, predicates) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation); page.on('request', started);
  try {
    const waiting = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const result = await Promise.all([...waiting, page.reload()]);
    return await Promise.all(result.slice(0, predicates.length).map(response => successfulJson(response)));
  } finally {
    page.off('framenavigated', navigation); page.off('request', started);
  }
}
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
  const errors = [], downloads = [], actualWrites = [], allowedWrites = [], previews = [], scopeChoices = [];
  let pendingWrite = null, previewPermit = null, authPhase = 'anonymous';
  const origins = new Set([base, 'http://127.0.0.1:18000']);
  const sessions = new Set([...origins].map(origin => `${origin}/api/auth/session`));
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('download', download => downloads.push(download));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      if (/status of 401/.test(message.text()) && sessions.has(message.location().url)
        && authPhase === 'anonymous') return;
      errors.push(`console: ${message.text()} ${message.location().url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && authPhase === 'anonymous') return;
    errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  context.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/accounting-scopes/page') scopeChoices.push(url);
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!origins.has(url.origin) || url.protocol !== 'http:') {
      errors.push(`Forbidden resource: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    // Even GET exports/PDFs are forbidden; T53 creates drafts, not documents.
    if (url.pathname.startsWith('/api/reports/') || /\/(?:pdf|send|issue)(?:\/|$)/.test(url.pathname)) {
      errors.push(`Forbidden export/issue/send: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    if (url.pathname === '/api/dunning/batch/preview') {
      previews.push(request);
      const permit = previewPermit;
      let matches = permit && !permit.seen && request.method() === 'POST'
        && url.origin === base && !url.search;
      if (matches) {
        try { assert.deepEqual(request.postDataJSON(), permit.body); }
        catch (error) { errors.push(`Unexpected preview body: ${error.message}`); matches = false; }
      }
      if (!matches) {
        errors.push(`Unexpected batch preview: ${request.method()} ${request.url()}`);
        return route.abort('blockedbyclient');
      }
      permit.seen = true;
      return route.continue();
    }
    if (request.method() === 'GET') return route.continue();
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen || url.origin !== base || url.search) {
      errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    if (pendingWrite.validate) {
      try { pendingWrite.validate(request.postDataJSON()); }
      catch (error) {
        errors.push(`Unexpected write body: ${error.message}`);
        return route.abort('blockedbyclient');
      }
    }
    pendingWrite.seen = true;
    return route.continue();
  });
  assert.equal(typeof context.routeWebSocket, 'function', 'Playwright must support WebSocket blocking');
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
      && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
      socket.connectToServer(); return; // Only local Vite HMR.
    }
    errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
  });
  const assertClean = () => {
    assert.deepEqual(errors, [], 'No unexpected network/console/page errors or mutations');
    assert.equal(downloads.length, 0, 'No PDF/export/download in draft-only T53');
  };
  const page = await context.newPage();
  async function uiWrite(method, pathname, action, status = 201, validate = null) {
    assert.equal(pendingWrite, null); assert.equal(previewPermit, null);
    const permit = { key: `${method} ${pathname}`, validate, seen: false };
    pendingWrite = permit; allowedWrites.push(permit.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(permit.seen, true, 'Actual UI must consume exactly one write permit');
      const json = await successfulJson(response, status);
      assertClean(); return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }
  await page.goto(`${base}/objekte`);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN'); authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`); await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Mahnweg 53'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  // Canonical property-creation UI grants this fixed four-capability audience.
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T53 synthetischer Mahnlauf; ausdrückliche Quellrechte, kein Versand');
  const { json: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties', () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyPath = `/objekte/${property.id}`;
  assert.equal(new URL(page.url()).pathname, propertyPath);
  const units = [], people = [], tenants = [], leases = [], unitPaths = [], personPaths = [];
  for (let i = 0; i < 3; i++) {
    await page.goto(base + propertyPath); await button(page, '+ Neue Einheit').click();
    const unitName = `${prefix} Wohnung ${i + 1}`;
    await field(page, 'Nummer *').fill(unitName); await field(page, 'Flaeche (m2)').fill('70');
    const { json: unit } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
    assert.equal(unit.property_id, property.id); units.push(unit);
    await page.getByRole('link', { name: unitName, exact: true }).click();
    unitPaths.push(new URL(page.url()).pathname);
    assert.ok(unitPaths[i].endsWith(`/${unit.id}`));
    const firstName = `${prefix} ${['Ada', 'Berta', 'Clara'][i]}`, lastName = 'Mahnlaufmieter';
    await page.goto(`${base}/personen`); await button(page, '+ Neue Person').click();
    for (const [label, value] of [['Vorname', firstName], ['Nachname *', lastName],
      ['Strasse', `Synthetischer Mieterweg ${i + 1}`], ['PLZ', '12346'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
    const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
    assert.equal(person.first_name, firstName); assert.equal(person.last_name, lastName); people.push(person);
    await searchList(page, 'Personen suchen');
    await page.getByRole('link', { name: `${firstName} ${lastName}`, exact: true }).click();
    personPaths.push(new URL(page.url()).pathname);
    await page.goto(`${base}/kontaktdaten-rechte`);
    await field(page, 'Kontakt suchen').fill(firstName); await selectId(page, 'Kontakt', person.id);
    await selectLabel(page, 'Begründung', 'Ersteinrichtung');
    const { json: domain } = await uiWrite('POST', '/api/person-data-domains', () => button(page, 'Datenbereich vorbereiten').click());
    assert.equal(domain.person_id, person.id);
    await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
    await field(page, 'Teammitglied suchen').fill('test@example.de'); await selectId(page, 'Teammitglied', principal.user_id);
    await field(page, 'Bearbeiten').check(); await field(page, 'Exportieren').check();
    await selectLabel(page, 'Begründung', 'Teamzugriff');
    const { json: contactGrant, payload } = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
      () => button(page, 'Teamfreigabe vorbereiten').click());
    assert.equal(contactGrant.user_id, principal.user_id); assert.equal(payload.user_id, principal.user_id);
    assert.deepEqual([...contactGrant.capabilities].sort(), ['export', 'prepare', 'read']);
    assert.deepEqual([...payload.capabilities].sort(), ['export', 'prepare', 'read']);
    await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
    await page.goto(base + personPaths[i]);
    const { json: tenant } = await uiWrite('POST', '/api/tenant-profiles', () => button(page, 'Als Mieter kennzeichnen').click());
    assert.equal(tenant.person_id, person.id); tenants.push(tenant);
    await page.goto(base + unitPaths[i]); await button(page, '+ Mietvertrag').click();
    await selectId(page, 'Mieter *', tenant.id);
    for (const [label, value] of [['Vertragsbeginn *', '2026-10-01'], ['Vertragsende', ''],
      ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '150.00']]) await field(page, label).fill(value);
    const { json: lease } = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click());
    assert.equal(lease.unit_id, unit.id); assert.equal(lease.tenant_profile_id, tenant.id);
    assert.equal(lease.valid_from, '2026-10-01'); assert.equal(lease.valid_to, null);
    money(lease.base_rent, 70000); money(lease.advance_payment, 15000); leases.push(lease);
  }
  for (const rows of [units, people, tenants, leases]) assert.equal(new Set(rows.map(row => row.id)).size, 3);
  await page.goto(`${base}/berechtigungen`);
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName); await field(page, 'Bezeichnung').fill(scopeName);
  await selectLabel(page, 'Verwaltungsart', 'MIET'); await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill(''); await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await uiWrite('POST', '/api/accounting-scopes', () => button(page, 'Bereich verbindlich anlegen').click());
  assert.equal(scope.property_id, property.id); assert.equal(scope.scope_type, 'MIET');
  assert.equal(scope.whole_property, true); assert.equal(scope.valid_from, '2026-01-01'); assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de'); await selectId(page, 'Teammitglied', principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten']) await field(grantForm, label).check();
  for (const label of ['Bestätigen', 'Exportieren', 'Freigeben', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T53 nur Lesen und Vorbereiten von Mahnentwürfen; kein Ausstellen, Export oder Versand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id); assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['prepare', 'read']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['prepare', 'read']);

  async function readDetail(pathname, uiPath) {
    await page.goto(base + uiPath);
    const [value] = await freshReload(page, [isApi('GET', pathname)]);
    assertClean(); return value;
  }
  async function businessSnapshot() {
    await page.goto(`${base}/offene-posten`);
    const paths = ['/api/leases', '/api/tenant-profiles', '/api/persons', '/api/units', '/api/properties'];
    const lists = await freshReload(page, paths.map(pathname => response =>
      isApi('GET', pathname)(response) && !new URL(response.url()).search));
    const snapshot = Object.fromEntries(paths.map((pathname, i) => [pathname, sorted(lists[i])]));
    await page.goto(`${base}/zahlungen`);
    const [payments, banks] = await freshReload(page, ['/api/payments', '/api/bank-accounts'].map(pathname => response =>
      isApi('GET', pathname)(response) && !new URL(response.url()).search));
    snapshot.payments = sorted(payments); snapshot.banks = sorted(banks);
    snapshot.paymentDetails = [];
    for (const payment of snapshot.payments) snapshot.paymentDetails.push(
      await readDetail(`/api/payments/${payment.id}`, `/zahlungen/${payment.id}`));
    snapshot.property = await readDetail(`/api/properties/${property.id}`, propertyPath);
    snapshot.people = []; snapshot.unitSources = [];
    for (let i = 0; i < 3; i++) {
      snapshot.people.push(await readDetail(`/api/persons/${people[i].id}`, personPaths[i]));
      await page.goto(base + unitPaths[i]);
      const [unit, unitLeases, history] = await freshReload(page, [isApi('GET', `/api/units/${units[i].id}`),
        response => isApi('GET', '/api/leases')(response) && new URL(response.url()).searchParams.get('unit_id') === units[i].id,
        isApi('GET', `/api/leases/${leases[i].id}/rent-changes`)]);
      assert.ok(unitLeases.some(value => value.id === leases[i].id)); assert.deepEqual(history, []);
      snapshot.unitSources.push({ unit, leases: sorted(unitLeases), history });
      assert.ok(snapshot['/api/persons'].some(value => value.id === people[i].id));
      assert.ok(snapshot['/api/tenant-profiles'].some(value => value.id === tenants[i].id));
      assert.ok(snapshot['/api/leases'].some(value => value.id === leases[i].id));
      assert.equal(snapshot.payments.some(value => value.lease_agreement_id === leases[i].id), false,
        'Synthetic leases remain completely unpaid');
    }
    assertClean(); return snapshot;
  }
  async function noticeSnapshot() {
    await page.goto(`${base}/mahnungen`);
    const [notices] = await freshReload(page, [response => isApi('GET', '/api/dunning')(response)
      && !new URL(response.url()).search]);
    assertClean(); return sorted(notices);
  }
  const before = await businessSnapshot(), noticesBefore = await noticeSnapshot();
  const writesBeforeBatch = [...actualWrites];
  assert.equal(writesBeforeBatch.length, 22, 'Complete login/property/3 units/3 contacts+domains+grants+profiles+leases/scope+grant setup');
  const input = { period_start: '2026-10-01', period_end: '2026-10-31', reference_date: '2026-11-15',
    threshold: '100.00', grace_days: 0, base_interest_rate: null, accounting_scope_id: scope.id };
  // Inherited period controls live on OpenItemsPage, outside the batch disclosure.
  // Use the region added by DunningBatch; never select the read-only scope above it.
  const batch = () => page.getByRole('region', { name: 'Mahnlauf', exact: true });
  const threshold = () => field(batch(), 'Mindestrückstand (EUR) *');
  const candidate = i => batch().getByRole('checkbox', { name: new RegExp(`${people[i].first_name} ${people[i].last_name}`) });
  async function openBatch() {
    await page.goto(`${base}/offene-posten`);
    await button(page, 'Mahnlauf öffnen').waitFor();
    assert.equal(await button(page, 'Mahnlauf prüfen').count(), 0, 'Batch initially closed after real navigation');
    await field(page, 'Zeitraum von *').fill(input.period_start);
    await field(page, 'Zeitraum bis *').fill(input.period_end);
    await button(page, 'Mahnlauf öffnen').click();
    await batch().waitFor();
    assert.equal(await threshold().inputValue(), '');
    assert.equal(await field(batch(), 'Stichtag *').inputValue(), '');
    await selectId(batch(), 'Immobilie für Abrechnungskreis', property.id);
    const scopeResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return isApi('GET', '/api/accounting-scopes/page')(response)
        && url.searchParams.get('property_id') === property.id && url.searchParams.get('capability') === 'prepare'
        && url.searchParams.get('scope_type') === 'MIET' && url.searchParams.get('q') === scopeName;
    });
    await field(batch(), 'Abrechnungskreis suchen').fill(scopeName);
    await successfulJson(await scopeResponse);
    await selectLabel(batch(), 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    await field(batch(), 'Stichtag *').fill(input.reference_date);
    await field(batch(), 'Karenztage').fill('0'); await threshold().fill(input.threshold);
    assert.equal(await batch().getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).inputValue(), scope.id);
  }
  async function previewBatch() {
    assert.equal(previewPermit, null); assert.equal(pendingWrite, null);
    const permit = { body: input, seen: false }; previewPermit = permit;
    let result;
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi('POST', '/api/dunning/batch/preview')),
        button(batch(), 'Mahnlauf prüfen').click()]);
      assert.equal(permit.seen, true); result = await successfulJson(response);
    } finally { previewPermit = null; }
    assert.match(result.source_digest, /^[a-f0-9]{64}$/);
    assert.equal(result.candidates.length, 3);
    assert.deepEqual(result.candidates.map(value => value.lease_agreement_id).sort(), leases.map(value => value.id).sort());
    for (let i = 0; i < 3; i++) {
      const row = result.candidates.find(value => value.lease_agreement_id === leases[i].id);
      assert.equal(row.tenant_profile_id, tenants[i].id);
      assert.equal(row.person_name, `${people[i].first_name} ${people[i].last_name}`);
      assert.equal(row.unit_number, units[i].number); assert.equal(row.property_name, propertyName);
      money(row.principal_amount, 85000); money(row.fee_amount, 0); money(row.interest_amount, 0); money(row.total_amount, 85000);
      await candidate(i).waitFor(); assert.equal(await candidate(i).isChecked(), false, 'Selection must be explicit');
    }
    assert.equal(await button(batch(), 'Auswahl bestätigen').isDisabled(), true);
    assert.deepEqual(actualWrites, writesBeforeBatch, 'Preview never mutates sources or notices');
    assertClean(); return result;
  }
  async function confirmSelection() {
    for (let i = 0; i < 3; i++) { await candidate(i).check(); assert.equal(await candidate(i).isChecked(), true); }
    await button(batch(), 'Auswahl bestätigen').click();
    const dialog = page.getByRole('dialog', { name: 'Mahnentwürfe anlegen', exact: true });
    await dialog.waitFor();
    assert.match(await dialog.innerText(), /3 Mahnentwürfe/);
    assert.equal(((await dialog.innerText()).match(/2550[.,]00 EUR/g) || []).length, 2,
      'Confirmation shows fixed 2550.00 for both principal and total');
    return dialog;
  }
  await openBatch();
  // Invalid local input must block the actual preview and any mutation.
  const beforeInvalid = previews.length;
  if ((await threshold().getAttribute('type')) === 'number') await threshold().fill('');
  else await threshold().fill('NaN');
  assert.equal(await button(batch(), 'Mahnlauf prüfen').isDisabled(), true);
  assert.equal(previews.length, beforeInvalid); assert.deepEqual(actualWrites, writesBeforeBatch);
  await threshold().fill('100.00'); await previewBatch();
  await candidate(0).check(); await threshold().fill('101.00');
  await eventually(async () => assert.equal(await batch().getByRole('checkbox').count(), 0,
    'Changing input discards the reviewed source digest and selection'));
  assert.equal(await button(batch(), 'Auswahl bestätigen').count(), 0);
  assert.equal(previews.length, beforeInvalid + 1, 'Editing inputs must not silently re-preview');
  assert.deepEqual(actualWrites, writesBeforeBatch);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload(); // Actual phone document; no stale desktop selections.
  await openBatch(); await previewBatch();
  const phoneDialog = await confirmSelection();
  await button(phoneDialog, 'Abbrechen').click();
  await phoneDialog.waitFor({ state: 'hidden' });
  await button(page, 'Mahnlauf schließen').click();
  assert.equal(await button(page, 'Mahnlauf prüfen').count(), 0);
  await page.reload();
  await button(page, 'Mahnlauf öffnen').waitFor();
  assert.equal(await button(page, 'Mahnlauf prüfen').count(), 0);
  assert.deepEqual(actualWrites, writesBeforeBatch, 'Phone selection/cancel/reload must never create notices');
  assert.deepEqual(await noticeSnapshot(), noticesBefore, 'Preview/edit/phone cancel create no notices');
  assert.deepEqual(await businessSnapshot(), before, 'Preview/edit/phone cancel leave all captured source DTOs unchanged');

  await page.setViewportSize({ width: 1440, height: 1000 });
  await openBatch(); const preview = await previewBatch();
  const dialog = await confirmSelection();
  const beforeCreateCount = actualWrites.filter(key => key === 'POST /api/dunning/batch').length;
  let confirmationClicks = 0;
  const { json: created, payload: batchPayload } = await uiWrite('POST', '/api/dunning/batch', async () => {
    confirmationClicks++;
    await button(dialog, 'Mahnentwürfe anlegen').click({ clickCount: 1 });
  }, 201, payload => {
    assert.match(payload.request_id, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i);
    assert.deepEqual([...payload.lease_agreement_ids].sort(), leases.map(value => value.id).sort());
    assert.equal(payload.lease_agreement_ids.length, 3);
    assert.deepEqual(payload, { ...input, lease_agreement_ids: payload.lease_agreement_ids,
      expected_source_digest: preview.source_digest, request_id: payload.request_id });
  });
  assert.equal(confirmationClicks, 1);
  assert.equal(actualWrites.filter(key => key === 'POST /api/dunning/batch').length - beforeCreateCount, 1);
  assert.equal(created.length, 3); assert.equal(new Set(created.map(value => value.id)).size, 3);
  assert.equal(batchPayload.expected_source_digest, preview.source_digest);
  assert.deepEqual(created.map(value => value.tenant_profile_id).sort(), tenants.map(value => value.id).sort());
  function assertDraft(value) {
    assert.equal(value.status, 'draft'); assert.equal(value.issued_at, null); assert.equal(value.issued_by, null);
    assert.equal(value.accounting_scope_id, scope.id); assert.equal(value.owner_profile_id, null);
    money(value.principal_amount, 85000); money(value.fee_amount, 0); money(value.interest_amount, 0); money(value.total_amount, 85000);
  }
  for (const notice of created) {
    assertDraft(notice);
    const link = page.getByRole('link', { name: `Entwurf ${notice.id} öffnen`, exact: true });
    await link.waitFor(); assert.equal(await link.getAttribute('href'), `/mahnungen/${notice.id}`);
  }
  // Exact integer-cent total, independent of UI's summation or binary floats.
  const total = key => created.reduce((sum, notice) => {
    money(notice[key], 85000); return sum + 85000n;
  }, 0n);
  assert.equal(total('principal_amount'), 255000n); assert.equal(total('total_amount'), 255000n);
  const noticesAfter = await noticeSnapshot();
  const newIds = new Set(created.map(value => value.id));
  assert.equal(noticesAfter.length, noticesBefore.length + 3);
  assert.deepEqual(noticesAfter.filter(value => !newIds.has(value.id)), noticesBefore,
    'Every pre-existing notice remains byte-for-byte DTO equivalent');
  assert.deepEqual(sorted(noticesAfter.filter(value => newIds.has(value.id))), sorted(created));
  for (const notice of created) {
    const row = page.getByRole('row').filter({ has: page.locator(`a[href="/mahnungen/${notice.id}"]`) });
    await row.getByText('Entwurf', { exact: true }).waitFor();
    const tenantIndex = tenants.findIndex(value => value.id === notice.tenant_profile_id);
    await row.getByRole('cell', { name: `${people[tenantIndex].first_name} ${people[tenantIndex].last_name}`, exact: true }).waitFor();
    const link = row.getByRole('link', { name: 'Details', exact: true });
    assert.equal(await link.getAttribute('href'), `/mahnungen/${notice.id}`);
    const [response] = await Promise.all([page.waitForResponse(isApi('GET', `/api/dunning/${notice.id}`)), link.click()]);
    const detail = await successfulJson(response); assertDraft(detail); assert.deepEqual(detail, notice);
    assert.equal(new URL(page.url()).pathname, `/mahnungen/${notice.id}`);
    await noticeSnapshot(); // Fresh history and actual link for every exact ID.
  }
  assert.deepEqual(await businessSnapshot(), before,
    'Full payments/payment details, leases, rent history, persons/profiles, units, banks and property DTOs unchanged');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.deepEqual(await noticeSnapshot(), noticesAfter, 'Phone real history reload persists the same three drafts only');
  await page.goto(`${base}/offene-posten`); await page.reload();
  await button(page, 'Mahnlauf öffnen').waitFor();
  assert.equal(await button(page, 'Mahnlauf prüfen').count(), 0);
  assert.equal(actualWrites.filter(key => key === 'POST /api/dunning/batch').length, 1, 'No second creation on any reload');
  assert.equal(previews.length, 3, 'Desktop preview/invalidation, phone preview/cancel, final desktop preview');
  assert.ok(scopeChoices.some(url => url.searchParams.get('property_id') === property.id
    && url.searchParams.get('capability') === 'prepare' && url.searchParams.get('scope_type') === 'MIET'
    && url.searchParams.get('financial_creation') === 'true'), 'Explicit property-bound MIET PREPARE selector');
  assert.deepEqual(actualWrites, allowedWrites); assert.equal(actualWrites.length, 23);
  assertClean();
  console.log(`T53 UI PASS: ${prefix}; 3 distinct tenants/units/leases; October 2026 unpaid 850.00 each; exact 2550.00 principal/total; input rejection/invalidation; 390x844 selection/cancel/reload; one actual confirmation click and one batch POST created exactly 3 persisted unissued drafts (${created.map(value => value.id).join(', ')}); canonical source DTOs and prior notices unchanged; no PDF/issue/send/export/API fixture/external requests.`);
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
