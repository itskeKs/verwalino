#!/usr/bin/env node
/* T35: UI-only tenant dunning, frozen interest snapshot and actual Chrome PDF.
 * Root must start the app separately (UI 13000/API 18000). This script starts
 * no server, seeds no API/DB, and never changes payments, leases or history.
 * Run: node scripts/acceptance/t35_dunning_interest_browser.cjs
 * Requires test@example.de/testpassword, Chrome, Playwright and backend uv with
 * pdfplumber. Synthetic records, including the legitimately issued notice,
 * remain. The only direct request is a read-only reread of the UI-opened PDF.
 * New T35 contract: text field "Basiszinssatz (%)", comma input; optional
 * GET /api/dunning/settings base_interest_rate; frozen interest_snapshot.
 * The existing source calls issuance "Mahnung ausgeben" (native confirm).
 * No fallback selectors: missing/changed accessible labels must fail at Root.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T35 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} MIET`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} Mietkreis`;
const firstName = `${prefix} Ada`;
const lastName = 'Zinsmieter';
const personName = `${firstName} ${lastName}`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
// Decimal strings only; no Number/float conversion, rounding or locale parsing.
function decimal(value) {
  assert.equal(typeof value, 'string', 'DTO decimal must be a string');
  assert.match(value, /^-?\d+(?:\.\d+)?$/);
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  const integer = whole.replace(/^0+(?=\d)/, '');
  const tail = fraction.replace(/0+$/, '');
  const token = integer + (tail ? `.${tail}` : '');
  return negative && token !== '0' ? `-${token}` : token;
}
const equalDecimal = (actual, expected) => assert.equal(decimal(actual), decimal(expected));
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
  if (response.status() !== status) {
    assert.fail(`${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}
async function selectLabel(page, name, label) {
  const select = page.getByRole('combobox', { name, exact: true });
  await select.getByRole('option', { name: label, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label });
}
async function selectId(page, name, id) {
  const select = page.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${name}: missing observed ID ${id}`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
}
// Only responses to requests started after the new main document committed.
async function freshReload(page, predicates) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    const pending = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const result = await Promise.all([...pending, page.reload()]);
    return await Promise.all(result.slice(0, predicates.length).map(response => successfulJson(response)));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
  }
}
function interestSnapshot(value) {
  assert.ok(value && typeof value === 'object');
  return { ...value, base_interest_rate: decimal(value.base_interest_rate),
    annual_rate: decimal(value.annual_rate), items: value.items.map(item => ({ ...item,
      principal: decimal(item.principal), interest: decimal(item.interest) })) };
}
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], authResponses = [], authConsole = [];
  let authPhase = 'anonymous';
  let pendingWrite = null;
  const sessions = new Set([`${base}/api/auth/session`, 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) authConsole.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) {
      authResponses.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  // Abort unexpected writes before they can change data, not just log them.
  await context.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() === 'GET') return route.continue();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen
      || url.origin !== base || url.search) {
      errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    pendingWrite.seen = true;
    return route.continue();
  });
  const assertClean = () => assert.deepEqual(errors, [], 'No unexpected HTTP/console/page errors or writes');
  const page = await context.newPage();
  async function uiWrite(method, pathname, action, status = 201) {
    assert.equal(pendingWrite, null);
    const expected = { key: `${method} ${pathname}`, seen: false };
    pendingWrite = expected;
    allowedWrites.push(expected.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(expected.seen, true, 'Allowed write must originate from the actual UI');
      const json = status === 204 ? null : await successfulJson(response, status);
      assert.equal(response.status(), status);
      assertClean();
      return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }

  await page.goto(`${base}/objekte`); // Avoid unrelated dashboard/T42 409.
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Zinsweg 35'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T35 synthetische Mietmahnung mit ausdruecklichen Quellrechten');
  const { json: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties',
    () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  assert.equal(new URL(page.url()).pathname, `/objekte/${property.id}`);
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  const { json: unit } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
  assert.equal(unit.property_id, property.id);
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitUrl = page.url();
  assert.ok(new URL(unitUrl).pathname.endsWith(`/${unit.id}`));

  await page.goto(`${base}/personen`);
  await button(page, '+ Neue Person').click();
  for (const [label, value] of [['Vorname', firstName], ['Nachname *', lastName],
    ['Strasse', 'Mieterweg 35'], ['PLZ', '12346'], ['Stadt', 'Mieterstadt']]) await field(page, label).fill(value);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Mieterweg 35', '12346', 'Mieterstadt']);
  await searchList(page, 'Personen suchen');
  await page.getByRole('link', { name: personName, exact: true }).click();
  const personUrl = page.url();
  await page.goto(`${base}/kontaktdaten-rechte`);
  await field(page, 'Kontakt suchen').fill(firstName); // SearchChoice searches first name, not concatenated name.
  await selectId(page, 'Kontakt', person.id);
  await selectLabel(page, 'Begründung', 'Ersteinrichtung');
  const { json: domain } = await uiWrite('POST', '/api/person-data-domains',
    () => button(page, 'Datenbereich vorbereiten').click());
  assert.equal(domain.person_id, person.id);
  await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  await field(page, 'Bearbeiten').check();
  await field(page, 'Exportieren').check();
  await selectLabel(page, 'Begründung', 'Teamzugriff');
  const { json: contactGrant, payload: contactPayload } = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
    () => button(page, 'Teamfreigabe vorbereiten').click());
  assert.equal(contactGrant.user_id, principal.user_id);
  assert.equal(contactPayload.user_id, principal.user_id);
  assert.deepEqual([...contactGrant.capabilities].sort(), ['export', 'prepare', 'read']);
  assert.deepEqual(contactPayload.capabilities, ['export', 'prepare', 'read']);
  await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
  await page.goto(personUrl);
  const { json: tenant } = await uiWrite('POST', '/api/tenant-profiles',
    () => button(page, 'Als Mieter kennzeichnen').click());
  assert.equal(tenant.person_id, person.id);
  await page.goto(unitUrl);
  await button(page, '+ Mietvertrag').click();
  await selectId(page, 'Mieter *', tenant.id);
  for (const [label, value] of [['Vertragsbeginn *', '2026-01-01'], ['Vertragsende', ''],
    ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '0.00']]) await field(page, label).fill(value);
  const { json: lease } = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click());
  assert.equal(lease.unit_id, unit.id);
  assert.equal(lease.tenant_profile_id, tenant.id);
  assert.equal(lease.valid_from, '2026-01-01');
  assert.equal(lease.valid_to, null);
  equalDecimal(lease.base_rent, '700.00');
  equalDecimal(lease.advance_payment, '0.00');

  await page.goto(`${base}/berechtigungen`);
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName);
  await field(page, 'Bezeichnung').fill(scopeName);
  await selectLabel(page, 'Verwaltungsart', 'MIET');
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await uiWrite('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click());
  assert.equal(scope.property_id, property.id);
  assert.equal(scope.scope_type, 'MIET');
  assert.equal(scope.whole_property, true);
  assert.equal(scope.valid_from, '2026-01-01');
  assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten', 'Freigeben', 'Exportieren']) await field(grantForm, label).check();
  for (const label of ['Bestätigen', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T35 eigene MIET-Rechte zum Lesen, Vorbereiten, Ausstellen und PDF-Export; kein Versand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id);
  assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['approve', 'export', 'prepare', 'read']);

  async function leaseSnapshot() {
    await page.goto(unitUrl);
    const [leases, history] = await freshReload(page, [response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id,
    isApi('GET', `/api/leases/${lease.id}/rent-changes`)]);
    assert.equal(leases.filter(item => item.id === lease.id).length, 1);
    assertClean();
    return { leases, history };
  }
  const originalLease = await leaseSnapshot();
  await page.goto(`${base}/mahnungen`);
  const [settingsResponse] = await Promise.all([page.waitForResponse(isApi('GET', '/api/dunning/settings')),
    button(page, '+ Neue Mahnung').click()]);
  const settings = await successfulJson(settingsResponse);
  await selectLabel(page, 'Empfaengertyp *', 'Mieter');
  await selectId(page, 'Mieter *', tenant.id);
  await selectId(page, 'Mietvertrag *', lease.id);
  for (const [label, value] of [['Zeitraum von *', '2026-01-01'], ['Zeitraum bis *', '2026-01-31'],
    ['Stichtag', '2026-03-02'], ['Karenzzeit (Tage)', '0'], ['Mahngebuehr (Override)', '0']]) await field(page, label).fill(value);
  await field(page, 'Abrechnungskreis suchen').fill(scopeName);
  await selectId(page, 'Abrechnungskreis', scope.id);
  const basis = field(page, 'Basiszinssatz (%)');
  assert.equal(await basis.getAttribute('type'), 'text', 'Comma percent input is a real text field');
  if (settings.base_interest_rate == null) assert.equal(await basis.inputValue(), '', 'No configuration means blank, not implicit zero');
  else equalDecimal((await basis.inputValue()).replace(',', '.'), settings.base_interest_rate);
  for (const value of ['', 'keine-zahl']) {
    await basis.fill(value);
    const writes = actualWrites.length;
    const submit = button(page, 'Mahnung erstellen');
    if (await submit.isEnabled()) await submit.click();
    else assert.equal(await submit.isDisabled(), true);
    await page.waitForTimeout(300);
    assert.equal(actualWrites.length, writes, `Invalid basis ${JSON.stringify(value)} must not POST`);
    assert.equal(await basis.inputValue(), value);
    assertClean();
  }
  await basis.fill('2,50');
  const { json: notice, payload } = await uiWrite('POST', '/api/dunning/tenant',
    () => button(page, 'Mahnung erstellen').click());
  assert.equal(payload.tenant_profile_id, tenant.id);
  assert.equal(payload.lease_agreement_id, lease.id);
  assert.equal(payload.accounting_scope_id, scope.id);
  assert.equal(payload.period_start, '2026-01-01');
  assert.equal(payload.period_end, '2026-01-31');
  assert.equal(payload.reference_date, '2026-03-02');
  assert.equal(payload.grace_days, 0);
  equalDecimal(payload.override_fee, '0');
  equalDecimal(payload.base_interest_rate, '2.50');
  const expectedInterest = { base_interest_rate: '2.5', annual_rate: '7.5', reference_date: '2026-03-02',
    items: [{ due_date: '2026-01-01', days: 60, principal: '700', interest: '8.63',
      payment_type: 'KALTMIETE', period_year: 2026, period_month: 1 }] };
  function assertNotice(value, status) {
    assert.equal(value.id, notice.id);
    assert.equal(value.accounting_scope_id, scope.id);
    assert.equal(value.tenant_profile_id, tenant.id);
    assert.equal(value.status, status);
    equalDecimal(value.principal_amount, '700.00');
    equalDecimal(value.fee_amount, '0.00');
    equalDecimal(value.interest_amount, '8.63');
    equalDecimal(value.total_amount, '708.63');
    assert.deepEqual(interestSnapshot(value.interest_snapshot), expectedInterest);
  }
  assertNotice(notice, 'draft');
  await page.waitForURL(`${base}/mahnungen/${notice.id}`);
  const detailUrl = page.url();
  async function detailSnapshot(status) {
    await page.goto(detailUrl);
    const [stored] = await freshReload(page, [isApi('GET', `/api/dunning/${notice.id}`)]);
    assertNotice(stored, status);
    const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Verzugszinsen', exact: true }) });
    await row.waitFor();
    assert.match(await row.innerText(), /8[.,]63\s*EUR/);
    assertClean();
    return stored;
  }
  const draft = await detailSnapshot('draft');
  assert.deepEqual(draft, notice, 'Reload preserves complete draft DTO and frozen interest');
  let confirmedIssue = false;
  page.once('dialog', async dialog => {
    try {
      assert.equal(dialog.type(), 'confirm');
      assert.equal(dialog.message(), 'Mahnung ausgeben? Nach der Ausgabe ist die Mahnung unveraenderbar (GoBD). '
        + 'Der Entwurf wird endgültig als Mahnung gespeichert. Die Ausstellung weist keine Zustellung nach.');
      confirmedIssue = true;
      await dialog.accept();
    } catch (error) { errors.push(`Issue dialog: ${error.message}`); await dialog.dismiss(); }
  });
  const { json: issued } = await uiWrite('POST', `/api/dunning/${notice.id}/issue`,
    () => button(page, 'Mahnung ausgeben').click(), 200);
  assert.equal(confirmedIssue, true);
  assertNotice(issued, 'issued');
  assert.deepEqual(issued.interest_snapshot, draft.interest_snapshot, 'Issuance does not recalculate interest');
  assert.ok(issued.issued_at);
  const beforePdf = await detailSnapshot('issued');
  assert.deepEqual(beforePdf, issued);
  assert.deepEqual(await leaseSnapshot(), originalLease, 'Draft/issue preserve lease and complete rent history');
  await detailSnapshot('issued');
  const writesBeforePdf = actualWrites.length;
  const pdfPath = `/api/dunning/${notice.id}/pdf`;
  const [pdfPage, pdfResponse] = await Promise.all([page.waitForEvent('popup'),
    context.waitForEvent('response', { predicate: isApi('GET', pdfPath) }),
    button(page, 'PDF herunterladen').click()]);
  assert.equal(pdfResponse.status(), 200);
  assert.match(pdfResponse.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
  assert.match(pdfResponse.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
  await pdfPage.waitForLoadState('load');
  assert.equal(pdfPage.url(), pdfResponse.url());
  assert.equal(pdfPage.url(), `${base}${pdfPath}`);
  assert.equal(await pdfPage.evaluate(() => document.contentType), 'application/pdf');
  const viewerUrl = 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html';
  await eventually(async () => assert.ok(pdfPage.frames().some(frame => frame.url() === viewerUrl), 'Chrome PDF viewer frame loaded'));
  await pdfPage.frames().find(frame => frame.url() === viewerUrl).locator('pdf-viewer#viewer').waitFor({ state: 'attached' });
  // Same session, exact UI-opened URL only; avoids CDP returning viewer HTML.
  const raw = await context.request.get(pdfPage.url());
  assert.equal(raw.url(), pdfPage.url());
  assert.equal(raw.status(), 200);
  assert.match(raw.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
  assert.match(raw.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
  const bytes = await raw.body();
  assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
  assert.ok(bytes.length < 25 * 1024 * 1024);
  const parsed = spawnSync('uv', ['run', 'python', '-c',
    'import io,json,sys,pdfplumber\nwith pdfplumber.open(io.BytesIO(sys.stdin.buffer.read())) as pdf:\n print(json.dumps([page.extract_text() or "" for page in pdf.pages]))'],
  { cwd: path.resolve(__dirname, '../../backend'), input: bytes, encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  assert.ifError(parsed.error);
  assert.equal(parsed.status, 0, parsed.stderr);
  const pages = JSON.parse(parsed.stdout);
  assert.ok(pages.length > 0 && pages.every(text => text.trim()), 'Valid, nonempty PDF pages');
  const text = pages.join(' ').replace(/\s+/g, ' ');
  assert.ok(text.includes(personName), 'PDF names actual tenant');
  for (const value of ['Mieterweg 35', '12346', 'Mieterstadt']) assert.ok(text.includes(value), `PDF missing ${value}`);
  for (const [description, pattern] of [
    ['principal', /700[.,]00\s*(?:EUR|€)/], ['base rate', /2[.,]50\s*%/],
    ['annual rate', /7[.,]50\s*%/], ['60 days', /\b60\b/],
    ['interest', /8[.,]63\s*(?:EUR|€)/], ['total', /708[.,]63\s*(?:EUR|€)/],
  ]) assert.match(text, pattern, `PDF missing ${description}`);
  assert.match(text, /Basiszinssatz/i);
  assert.match(text, /Verzugszins/i);
  assert.match(text, /Tage/i);
  await pdfPage.close();
  assert.deepEqual(await detailSnapshot('issued'), beforePdf, 'PDF GET and fresh reload preserve complete issued DTO');
  assert.deepEqual(await leaseSnapshot(), originalLease, 'PDF GET preserves leases and rent history exactly');
  assert.equal(actualWrites.length, writesBeforePdf, 'Export and subsequent observations are read-only');
  assert.deepEqual(actualWrites, allowedWrites);
  authPhase = 'logging-out';
  await uiWrite('POST', '/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await button(page, 'Anmelden').waitFor();
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Only exact anonymous/logout session 401s, paired 1:1');
  assert.deepEqual(actualWrites, allowedWrites);
  assertClean();
  console.log('PASS T35: actual UI MIET setup, explicit grants, blank/invalid basis rejection, comma basis, 700 + 8.63 interest, frozen issued PDF and unchanged lease/history.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
