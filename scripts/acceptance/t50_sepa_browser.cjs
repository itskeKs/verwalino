#!/usr/bin/env node
/* T50: actual UI-only synthetic SEPA CORE acceptance, NEVER a bank submission.
 * Parent owns the isolated test DB/UI13000/API18000. This script starts neither.
 * Public test-vector IBAN/creditor ID are NOT real account data, bank authority,
 * signed consent, actual pre-notification, financial instructions or permission.
 * Checkbox clicks below SIMULATE those prerequisites ONLY in synthetic test data.
 * No SQL, backend fixtures, direct API writes, request clients, output artifacts,
 * transmission, payment creation/booking, mandate-history updates or real funds.
 * Requires existing Chrome/Playwright and Python with lxml for offline schemas.
 * Author verification is node --check ONLY; parent must execute browser acceptance.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T50 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} SEPA MIET`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} Mietkreis`;
const firstName = `${prefix} Ada`;
const lastName = 'Berichtsmieter';
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
const TEST_IBAN = 'DE89370400440532013000'; // Public synthetic test vector only.
const CREDITOR_ID = 'DE98ZZZ09999999999'; // Public synthetic test vector only.
const REFERENCE = `SYNTHETIC-OCT2026-${randomUUID().slice(0, 8).toUpperCase()}`;
const ORIGINAL = 'Synthetic T50 original – no real collection consent';
const repoRoot = path.resolve(__dirname, '../..');
const schemas = [
  path.join(repoRoot, 'backend/tests/fixtures/sepa/pain.008.001.08.xsd'),
  path.join(repoRoot, 'backend/app/resources/sepa/EPC130-08_2025_V1.0_pain.008.001.08.xsd'),
];
// One random test message per run, <=35 SEPA characters; repeat download is an
// intentional local replay, never a second submission to a financial institution.
const messageId = `T50-${randomUUID().replace(/-/g, '').slice(0, 30)}`;
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ serviceWorkers: 'block',
    viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], downloads = [], scopeChoices = [];
  const readRequests = [], expectedFailures = [], expectedConsole = [];
  const admittedReads = new Map();
  let authPhase = 'anonymous', pendingWrite = null, readPermit = null;
  const authResponses = [], authConsole = [];
  const sessions = new Set([`${base}/api/auth/session`, 'http://127.0.0.1:18000/api/auth/session']);
  const origins = new Set([base, 'http://127.0.0.1:18000']);
  context.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/accounting-scopes/page') scopeChoices.push(url);
  });
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('download', download => downloads.push(download));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url) && authPhase === 'anonymous') {
        authConsole.push(url);
      } else if (/status of 422/.test(message.text())
        && url === `${base}/api/payments/sepa/preview`
        && [...admittedReads.values()].some(permit => permit.status === 422 && permit.seen)
        && expectedConsole.length === 0) {
        expectedConsole.push(url); // Only the one explicitly permitted missing-mandate preview.
      } else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    const permit = admittedReads.get(response.request());
    if (permit && permit.status === 422 && response.status() === 422) {
      expectedFailures.push(response.url());
    } else if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && authPhase === 'anonymous') authResponses.push(response.url());
    else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  // All resources, not merely API paths, are loopback-only. No bank/public-host
  // allowlist, no HTTP client, no WebSocket transport, no worker bypass.
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!origins.has(url.origin) || !['http:'].includes(url.protocol)) {
      errors.push(`Forbidden non-loopback resource: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    if (url.pathname === '/api/payments/sepa/preview' || url.pathname === '/api/payments/sepa.xml') {
      readRequests.push(request);
      const permit = readPermit;
      let matches = permit && !permit.seen && url.origin === base && !url.search
        && request.method() === 'POST' && url.pathname === permit.pathname;
      if (matches) {
        try { assert.deepEqual(request.postDataJSON(), permit.body); }
        catch (error) { matches = false; errors.push(`Unexpected SEPA body: ${error.message}`); }
      }
      if (!matches) {
        errors.push(`Unexpected read-only SEPA POST: ${request.url()}`);
        return route.abort('blockedbyclient');
      }
      permit.seen = true;
      admittedReads.set(request, permit);
      return route.continue(); // NOT a configuration/business-write permit.
    }
    if (request.method() === 'GET') return route.continue();
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen || url.origin !== base || url.search) {
      errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    if (pendingWrite.body) {
      try { assert.deepEqual(request.postDataJSON(), pendingWrite.body); }
      catch (error) {
        errors.push(`Unexpected configuration body: ${error.message}`);
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
      socket.connectToServer(); // Only the prescribed local Vite HMR channel.
      return;
    }
    errors.push(`Forbidden WebSocket: ${socket.url()}`);
    socket.close();
  });
  const assertClean = () => assert.deepEqual(errors, [], 'No unexpected network/console/page errors or writes');
  const page = await context.newPage();
  async function uiWrite(method, pathname, action, status = 201, body = null) {
    assert.equal(pendingWrite, null);
    const permit = { key: `${method} ${pathname}`, body, seen: false };
    pendingWrite = permit; allowedWrites.push(permit.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(permit.seen, true, 'Actual UI must consume the single-use write permit');
      const json = status === 204 ? null : await successfulJson(response, status);
      assert.equal(response.status(), status);
      assertClean();
      return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }
  async function exportRead(pathname, body, action, status = 200) {
    assert.equal(readPermit, null);
    assert.equal(pendingWrite, null);
    const permit = { pathname, body, status, seen: false };
    readPermit = permit;
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi('POST', pathname)), action()]);
      assert.equal(permit.seen, true, 'Actual UI must consume exact-body read-only POST permit');
      assert.equal(response.status(), status, await response.text());
      assert.ok((await response.request().allHeaders()).cookie, 'Real credentialed browser request');
      assertClean();
      return response;
    } finally { readPermit = null; }
  }
  await page.goto(`${base}/objekte`); // Avoid unrelated dashboard/T42 409.
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Testweg 50'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T50 synthetischer SEPA-Test mit Quellrechten; keine echte Einzugserlaubnis');
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
    ['Strasse', 'Synthetischer Mieterweg 50'], ['PLZ', '12346'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  assert.equal(await field(page, 'IBAN').count(), 1,
    'BLOCKED: contact IBAN UI field missing; parent must expose it; never bypass via API');
  await field(page, 'IBAN').fill(TEST_IBAN);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Synthetischer Mieterweg 50', '12346', 'Teststadt']);
  assert.equal(person.iban, TEST_IBAN);
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
    ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '150.00']]) await field(page, label).fill(value);
  const { json: lease } = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click());
  assert.equal(lease.unit_id, unit.id);
  assert.equal(lease.tenant_profile_id, tenant.id);
  assert.equal(lease.valid_from, '2026-01-01');
  assert.equal(lease.valid_to, null);
  equalDecimal(lease.base_rent, '700.00');
  equalDecimal(lease.advance_payment, '150.00');

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
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Exportieren']) await field(grantForm, label).check();
  for (const label of ['Freigeben', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T50 eigene MIET-Rechte Lesen, Vorbereiten, Bestätigen und lokaler XML-Testexport; kein Versand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id);
  assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);

  async function selectScope() {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scope.id);
  }
  async function readDetail(pathname, uiPath) {
    await page.goto(base + uiPath);
    const [value] = await freshReload(page, [isApi('GET', pathname)]);
    assertClean();
    return value;
  }
  const bankName = `${prefix} Laufendes Konto`;
  await page.goto(`${base}/bankkonten`);
  await button(page, '+ Neues Bankkonto').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectLabel(page, 'Kontotyp *', 'Laufendes Konto');
  await field(page, 'Bezeichnung *').fill(bankName);
  await field(page, 'IBAN').fill(TEST_IBAN);
  await selectScope();
  const { json: bank, payload: bankPayload } = await uiWrite('POST', '/api/bank-accounts',
    () => button(page, 'Speichern').click());
  assert.equal(bank.property_id, property.id);
  assert.equal(bank.account_type, 'LAUFEND');
  assert.equal(bankPayload.accounting_scope_id, scope.id);

  assert.equal(bank.iban, TEST_IBAN);
  const accountPath = `/api/payments/sepa/accounts/${bank.id}`;
  const ui = () => page.getByRole('region', { name: 'SEPA-Lastschrift', exact: true });
  const result = () => ui().getByRole('region', { name: 'Geprüfte SEPA-Datei', exact: true });
  const creditorForm = () => ui().locator('form').filter({ has: page.getByRole('heading', { name: 'Gläubiger und Bankvereinbarung', exact: true }) });
  const mandateForm = () => ui().locator('form').filter({ has: page.getByRole('heading', { name: 'Vorhandenes unterschriebenes Mandat registrieren', exact: true }) });
  const exportForm = () => ui().locator('form').filter({ has: page.getByRole('heading', { name: 'Monatsdatei vorbereiten', exact: true }) });
  const bankAgreement = () => creditorForm().getByRole('checkbox', { name: /^Gläubigerangaben und bestehende Lastschriftvereinbarung/ });
  const originalConfirmation = () => mandateForm().getByRole('checkbox', { name: /^Das unterschriebene Original liegt bereits beim Gläubiger vor/ });
  const noticeConfirmation = () => exportForm().getByRole('checkbox', { name: /^Die tatsächliche Vorabankündigung/ });
  const profileConfirmation = () => exportForm().getByRole('checkbox', { name: /^Bankprofil pain/ });
  const shorterConfirmation = () => exportForm().getByRole('checkbox', { name: /^Kürzere Frist/ });
  async function openSepa() {
    await page.goto(`${base}/zahlungen`);
    await button(page, 'SEPA-Lastschrift öffnen').click();
    assert.equal(await field(ui(), 'SEPA-Bankkonto *').inputValue(), '', 'No implicit bank');
    await selectId(ui(), 'SEPA-Bankkonto *', bank.id);
    await field(ui(), 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(ui(), 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(ui(), 'Abrechnungskreis').inputValue(), scope.id);
    const [response] = await Promise.all([page.waitForResponse(isApi('GET', accountPath)),
      button(ui(), 'SEPA-Konfiguration laden').click()]);
    const account = await successfulJson(response);
    await creditorForm().waitFor();
    assert.equal(account.bank.id, bank.id);
    assert.equal(account.bank.property_id, property.id);
    assert.equal(account.bank.iban, TEST_IBAN);
    assert.deepEqual(account.tenants.map(value => [value.tenant_profile_id, value.person_id, value.name, value.iban, value.city]),
      [[tenant.id, person.id, personName, TEST_IBAN, 'Teststadt']]);
    assert.ok(scopeChoices.some(url => url.searchParams.get('capability') === 'export'
      && url.searchParams.get('property_id') === property.id), 'Bank-object-bound EXPORT scope selector');
    assertClean();
    return account;
  }
  const creditor = { creditor_id: CREDITOR_ID, name: 'SyntheticVermietung',
    country: 'DE', town: 'Teststadt', bank_authorized: true };
  const emptyAccount = await openSepa();
  assert.equal(emptyAccount.creditor, null);
  assert.deepEqual(emptyAccount.mandates, []);
  for (const label of ['Gläubiger-ID *', 'Gläubigername *', 'Gläubiger-Land (ISO) *', 'Gläubigerort *']) {
    assert.equal(await field(creditorForm(), label).inputValue(), '', 'No implicit creditor data');
  }
  assert.equal(await bankAgreement().isChecked(), false);
  for (const [label, value] of [['Gläubiger-ID *', CREDITOR_ID], ['Gläubigername *', creditor.name],
    ['Gläubiger-Land (ISO) *', 'DE'], ['Gläubigerort *', 'Teststadt']]) await field(creditorForm(), label).fill(value);
  assert.equal(await button(creditorForm(), 'Gläubiger speichern').isDisabled(), true);
  // SYNTHETIC simulation only: there is NO actual bank agreement or authority.
  await bankAgreement().check();
  const persistedCreditor = page.waitForResponse(isApi('GET', accountPath));
  await uiWrite('PUT', `${accountPath}/creditor`, () => button(creditorForm(), 'Gläubiger speichern').click(), 200, creditor);
  const creditorAccount = await successfulJson(await persistedCreditor);
  assert.deepEqual(creditorAccount.creditor, creditor);
  assert.deepEqual(creditorAccount.mandates, []);
  await eventually(async () => assert.equal(await field(creditorForm(), 'Gläubiger-ID *').inputValue(), CREDITOR_ID));
  await mandateForm().waitFor();

  const requestBody = { bank_account_id: bank.id, accounting_scope_id: scope.id,
    year: 2026, month: 10, collection_date: '2026-10-30', message_id: messageId,
    pre_notification_on: '2026-10-15', notice_period_days: 14, shorter_period_agreed: false,
    pre_notification_confirmed: true, bank_profile_confirmed: true };
  async function fillExport() {
    for (const label of ['Jahr *', 'Einzugsdatum *', 'Nachrichten-ID *',
      'Vorabankündigung erfolgt am *', 'Ankündigungsfrist in Tagen *']) {
      assert.equal(await field(exportForm(), label).inputValue(), '', `${label}: no default`);
    }
    assert.equal(await field(exportForm(), 'Monat *').inputValue(), '');
    for (const checkbox of [noticeConfirmation(), profileConfirmation(), shorterConfirmation()]) {
      assert.equal(await checkbox.isChecked(), false, 'Confirmation must never default to checked');
    }
    await field(exportForm(), 'Jahr *').fill('2026');
    await selectLabel(exportForm(), 'Monat *', 'Oktober');
    for (const [label, value] of [['Einzugsdatum *', '2026-10-30'], ['Nachrichten-ID *', messageId],
      ['Vorabankündigung erfolgt am *', '2026-10-15'], ['Ankündigungsfrist in Tagen *', '14']]) {
      await field(exportForm(), label).fill(value);
    }
    assert.equal(await button(exportForm(), 'SEPA-Vorschau erstellen').isDisabled(), true);
    // Test-only simulations: no notice is sent and no genuine consent asserted.
    await noticeConfirmation().check();
    assert.equal(await button(exportForm(), 'SEPA-Vorschau erstellen').isDisabled(), true);
    await profileConfirmation().check();
    assert.equal(await shorterConfirmation().isChecked(), false, 'Use the full 14 days, no shorter-period fiction');
  }
  await fillExport();
  const missing = await exportRead('/api/payments/sepa/preview', requestBody,
    () => button(exportForm(), 'SEPA-Vorschau erstellen').click(), 422);
  assert.match(JSON.stringify(await missing.json()), /explicit active mandate/);
  await ui().getByRole('alert').waitFor();
  assert.equal(await result().count(), 0);
  assert.equal(downloads.length, 0, 'Missing mandate must never yield XML');

  await selectId(mandateForm(), 'Mieter für neues Mandat *', tenant.id);
  for (const label of ['Mandatsreferenz *', 'Unterschrieben am *', 'Schuldner-Land (ISO) *',
    'IBAN ausdrücklich bestätigen *', 'Original-Aufbewahrungsreferenz *']) {
    assert.equal(await field(mandateForm(), label).inputValue(), '', `${label}: explicit input required`);
  }
  assert.equal(await field(mandateForm(), 'Sequenz neues Mandat *').inputValue(), '');
  assert.equal(await originalConfirmation().isChecked(), false);
  for (const [label, value] of [['Mandatsreferenz *', REFERENCE], ['Unterschrieben am *', '2026-01-01'],
    ['Schuldner-Land (ISO) *', 'DE'], ['IBAN ausdrücklich bestätigen *', TEST_IBAN],
    ['Original-Aufbewahrungsreferenz *', ORIGINAL]]) await field(mandateForm(), label).fill(value);
  await selectLabel(mandateForm(), 'Sequenz neues Mandat *', 'FRST – erster Einzug');
  assert.equal(await field(mandateForm(), 'Tatsächlicher vorheriger Einzug (neues Mandat)').inputValue(), '');
  assert.equal(await field(mandateForm(), 'Tatsächlicher vorheriger Einzug (neues Mandat)').isDisabled(), true);
  assert.equal(await button(mandateForm(), 'Mandat registrieren').isDisabled(), true);
  // SIMULATED signed-original retention: ORIGINAL explicitly denies real consent.
  await originalConfirmation().check();
  const mandateBody = { tenant_profile_id: tenant.id, reference: REFERENCE,
    signed_on: '2026-01-01', sequence_type: 'FRST', last_used_on: null, debtor_country: 'DE',
    confirmed_iban: TEST_IBAN, original_reference: ORIGINAL, mandate_confirmed: true };
  const persistedMandate = page.waitForResponse(isApi('GET', accountPath));
  const { json: mandate } = await uiWrite('POST', `${accountPath}/mandates`,
    () => button(mandateForm(), 'Mandat registrieren').click(), 201, mandateBody);
  function assertAccount(account) {
    assert.deepEqual(account.creditor, creditor);
    assert.equal(account.bank.id, bank.id);
    assert.equal(account.bank.iban, TEST_IBAN);
    assert.equal(account.mandates.length, 1);
    assert.deepEqual(account.mandates, [mandate], 'Exact persisted mandate DTO, not optimistic local state');
    for (const [key, value] of Object.entries({ tenant_profile_id: tenant.id, reference: REFERENCE,
      signed_on: '2026-01-01', sequence_type: 'FRST', last_used_on: null,
      debtor_country: 'DE', original_reference: ORIGINAL, revoked_at: null, source_current: true })) {
      assert.equal(account.mandates[0][key], value, key);
    }
    assert.deepEqual(account.tenants.map(value => [value.tenant_profile_id, value.person_id, value.iban]),
      [[tenant.id, person.id, TEST_IBAN]]);
  }
  assertAccount(await successfulJson(await persistedMandate));
  await page.reload(); // A real document reload before re-reading the actual account GET.
  const baselineAccount = await openSepa();
  assertAccount(baselineAccount);
  await ui().getByRole('region', { name: `Mandat ${REFERENCE}`, exact: true }).waitFor();

  const sorted = values => [...values].sort((a, b) => a.id.localeCompare(b.id));
  async function businessSnapshot() {
    await page.goto(`${base}/zahlungen`);
    const paths = ['/api/payments', '/api/bank-accounts', '/api/properties', '/api/leases',
      '/api/tenant-profiles', '/api/owner-profiles', '/api/persons'];
    const lists = await freshReload(page, paths.map(pathname => response =>
      isApi('GET', pathname)(response) && !new URL(response.url()).search));
    const snapshot = Object.fromEntries(paths.map((pathname, i) => [pathname, sorted(lists[i])]));
    // Complete DTOs of all visible payments, not just totals/status projections.
    snapshot.paymentDetails = [];
    for (const payment of snapshot['/api/payments']) {
      snapshot.paymentDetails.push(await readDetail(`/api/payments/${payment.id}`, `/zahlungen/${payment.id}`));
    }
    assert.deepEqual(snapshot['/api/payments'].filter(value => value.bank_account_id === bank.id
      || value.lease_agreement_id === lease.id || value.tenant_profile_id === tenant.id), [],
    'Synthetic lease has no payment; export must collect the full 700 + 150 rent');
    assert.deepEqual(snapshot['/api/leases'].filter(value => value.id === lease.id), [lease]);
    assert.deepEqual(snapshot['/api/persons'].filter(value => value.id === person.id), [person]);
    await page.goto(unitUrl);
    const [unitLeases, history] = await freshReload(page, [response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id,
    isApi('GET', `/api/leases/${lease.id}/rent-changes`)]);
    assert.deepEqual(unitLeases.filter(value => value.id === lease.id), [lease]);
    assert.deepEqual(history, []);
    snapshot.unitLeases = sorted(unitLeases); snapshot.history = history;
    snapshot.person = await readDetail(`/api/persons/${person.id}`, new URL(personUrl).pathname);
    snapshot.account = await openSepa();
    assertAccount(snapshot.account);
    assert.deepEqual(snapshot.account, baselineAccount);
    assertClean();
    return snapshot;
  }
  const before = await businessSnapshot();
  const writesBeforeExports = actualWrites.length;
  await fillExport();
  function assertPreview(preview) {
    assert.equal(preview.total, '850.00');
    assert.deepEqual(preview.rows, [{ tenant_profile_id: tenant.id, name: personName,
      mandate_id: mandate.id, reference: REFERENCE, amount: '850.00', sequence_type: 'FRST', lease_ids: [lease.id] }]);
    assert.match(preview.source_digest, /^[a-f0-9]{64}$/);
  }
  async function preview() {
    const value = await successfulJson(await exportRead('/api/payments/sepa/preview', requestBody,
      () => button(exportForm(), 'SEPA-Vorschau erstellen').click()));
    assertPreview(value);
    await result().getByRole('heading', { name: 'SEPA-Vorschau', exact: true }).waitFor();
    await eventually(async () => {
      const rows = await result().getByRole('table', { name: 'SEPA-Vorschau', exact: true }).locator('tbody tr').all();
      assert.equal(rows.length, 1);
      assert.deepEqual(await rows[0].getByRole('cell').allTextContents(),
        [personName, REFERENCE, 'FRST', '850.00', lease.id]);
    });
    return value;
  }
  const firstPreview = await preview();
  const requestsBeforeEdit = readRequests.length;
  await field(exportForm(), 'Nachrichten-ID *').fill(`${messageId.slice(0, 33)}X`);
  await eventually(async () => assert.equal(await result().count(), 0, 'Editing export input invalidates preview and download'));
  assert.equal(readRequests.length, requestsBeforeEdit, 'Editing does not auto-export');
  await field(exportForm(), 'Nachrichten-ID *').fill(messageId);
  const checked = await preview();
  assert.equal(checked.source_digest, firstPreview.source_digest);
  const identifiers = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt === 1) {
      await page.setViewportSize({ width: 390, height: 844 });
      await result().getByRole('table', { name: 'SEPA-Vorschau', exact: true }).waitFor({ state: 'visible' });
      await button(result(), 'SEPA-XML herunterladen').scrollIntoViewIfNeeded();
      assert.equal(await button(result(), 'SEPA-XML herunterladen').isVisible(), true, 'Phone download action is usable');
    }
    const [download, response] = await Promise.all([page.waitForEvent('download'),
      exportRead('/api/payments/sepa.xml', { ...requestBody, expected_source_digest: checked.source_digest },
        () => button(result(), 'SEPA-XML herunterladen').click())]);
    assert.equal(download.suggestedFilename(), 'SEPA_2026_10.xml');
    assert.match(response.headers()['content-type'] || '', /^application\/xml(?:;|$)/i);
    assert.match(response.headers()['content-disposition'] || '', /^attachment\s*;/i);
    assert.match(response.headers()['content-disposition'], /SEPA_2026_10\.xml/);
    assert.equal(response.headers()['x-content-type-options'], 'nosniff');
    assert.equal(response.headers()['cache-control'], 'private, no-store');
    const stream = await download.createReadStream();
    assert.ok(stream, 'Parse actual Playwright download stream, never a substitute response');
    const chunks = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    assert.equal(await download.failure(), null);
    const bytes = Buffer.concat(chunks);
    const parsed = spawnSync(process.env.HV_PYTHON || 'python3', ['-c', String.raw`
import base64, json, re, sys
from decimal import Decimal
from lxml import etree
p = json.load(sys.stdin)
raw = base64.b64decode(p['bytes'], validate=True)
parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
root = etree.fromstring(raw, parser)
assert not root.getroottree().docinfo.doctype, 'No DTD'
ns = 'urn:iso:std:iso:20022:tech:xsd:pain.008.001.08'
n = {'s': ns}
assert root.tag == '{' + ns + '}Document'
assert all(etree.QName(e).namespace == ns for e in root.iter())
# Original ISO AND original EPC profiles, with network/entity resolution off.
# Do not import renderer/application code or alter/replace any schema bytes.
assert len(p['schemas']) == 2
for filename in p['schemas']:
    schema = etree.XMLSchema(etree.parse(filename, parser))
    schema.assertValid(root)
def one(node, xpath):
    values = node.xpath(xpath, namespaces=n)
    assert len(values) == 1, (xpath, values)
    return values[0]
def text(node, xpath, expected):
    element = one(node, xpath)
    assert element.text == expected, (xpath, element.text, expected)
initiation = one(root, './s:CstmrDrctDbtInitn')
header = one(initiation, './s:GrpHdr')
text(header, './s:MsgId', p['message_id'])
text(header, './s:NbOfTxs', '1')
text(header, './s:CtrlSum', '850.00')
text(header, './s:InitgPty/s:Nm', 'SyntheticVermietung')
payment = one(initiation, './s:PmtInf')
text(payment, './s:PmtMtd', 'DD')
text(payment, './s:NbOfTxs', '1')
text(payment, './s:CtrlSum', '850.00')
text(payment, './s:PmtTpInf/s:SvcLvl/s:Cd', 'SEPA')
text(payment, './s:PmtTpInf/s:LclInstrm/s:Cd', 'CORE')
text(payment, './s:PmtTpInf/s:SeqTp', 'FRST')
text(payment, './s:ReqdColltnDt', '2026-10-30')
text(payment, './s:Cdtr/s:Nm', 'SyntheticVermietung')
text(payment, './s:Cdtr/s:PstlAdr/s:Ctry', 'DE')
text(payment, './s:Cdtr/s:PstlAdr/s:TwnNm', 'Teststadt')
text(payment, './s:CdtrAcct/s:Id/s:IBAN', p['iban'])
text(payment, './s:CdtrSchmeId/s:Id/s:PrvtId/s:Othr/s:Id', p['creditor_id'])
text(payment, './s:ChrgBr', 'SLEV')
tx = one(payment, './s:DrctDbtTxInf')
assert len(root.xpath('.//s:DrctDbtTxInf', namespaces=n)) == 1
amount = one(tx, './s:InstdAmt')
assert amount.text == '850.00' and amount.get('Ccy') == 'EUR'
amounts = root.xpath('.//s:InstdAmt', namespaces=n)
assert len(amounts) == 1 and sum((Decimal(e.text) for e in amounts), Decimal('0.00')) == Decimal('850.00')
text(tx, './s:DrctDbtTx/s:MndtRltdInf/s:MndtId', p['reference'])
text(tx, './s:DrctDbtTx/s:MndtRltdInf/s:DtOfSgntr', '2026-01-01')
text(tx, './s:Dbtr/s:Nm', p['person_name'])
text(tx, './s:Dbtr/s:PstlAdr/s:Ctry', 'DE')
text(tx, './s:Dbtr/s:PstlAdr/s:TwnNm', 'Teststadt')
text(tx, './s:DbtrAcct/s:Id/s:IBAN', p['iban'])
assert root.xpath('.//s:IBAN/text()', namespaces=n) == [p['iban'], p['iban']]
e2e = root.xpath('.//s:EndToEndId/text()', namespaces=n)
assert len(e2e) == 1 and len(set(e2e)) == len(e2e), 'Exactly one unique transaction ID'
assert all(re.fullmatch(r'[A-Za-z0-9/?:().,\x27+ -]{1,35}', value) and value != 'NOTPROVIDED' for value in e2e)
payment_id = one(payment, './s:PmtInfId').text
assert payment_id and len(payment_id) <= 35
print(json.dumps({'message_id': p['message_id'], 'payment_id': payment_id, 'e2e': e2e,
                  'transactions': 1, 'total': '850.00', 'schemas': 2}))
`], { cwd: repoRoot, input: JSON.stringify({ bytes: bytes.toString('base64'), schemas,
      message_id: messageId, reference: REFERENCE, iban: TEST_IBAN, creditor_id: CREDITOR_ID, person_name: personName }),
      encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ifError(parsed.error);
    assert.equal(parsed.status, 0, parsed.stderr);
    const ids = JSON.parse(parsed.stdout);
    assert.equal(ids.message_id, messageId);
    assert.equal(ids.transactions, 1);
    assert.equal(ids.total, '850.00');
    assert.equal(ids.schemas, 2);
    identifiers.push(ids);
    assertClean();
  }
  assert.deepEqual(identifiers[1], identifiers[0], 'Local repeat preserves message/payment/E2E identities; NEVER upload both');
  assert.equal(downloads.length, 2);
  assert.equal(readRequests.length, 5, 'One missing-mandate preview, two valid previews, two XML downloads');
  assert.deepEqual(expectedFailures, [`${base}/api/payments/sepa/preview`]);
  assert.ok(expectedConsole.length <= 1);
  await page.setViewportSize({ width: 1440, height: 1000 });
  assert.deepEqual(await businessSnapshot(), before,
    'Full payment list/details, bank/property/person/profile/lease DTOs, rent history and SEPA config unchanged');
  assert.equal(actualWrites.length, writesBeforeExports, 'Preview and downloads never perform business/config mutations');
  const counts = new Map();
  for (const key of actualWrites) counts.set(key, (counts.get(key) || 0) + 1);
  const expectedWrites = new Map([
    ['POST /api/auth/login', 1], ['POST /api/properties', 1], ['POST /api/units', 1],
    ['POST /api/persons', 1], ['POST /api/person-data-domains', 1],
    [`POST /api/person-data-domains/${domain.id}/grants`, 1], ['POST /api/tenant-profiles', 1],
    ['POST /api/leases', 1], ['POST /api/accounting-scopes', 1],
    [`POST /api/accounting-scopes/${scope.id}/grants`, 1], ['POST /api/bank-accounts', 1],
    [`PUT ${accountPath}/creditor`, 1], [`POST ${accountPath}/mandates`, 1],
  ]);
  assert.deepEqual([...counts].sort(), [...expectedWrites].sort(), 'Only enumerated UI setup/config mutations');
  assert.deepEqual(actualWrites, allowedWrites, 'Every UI mutation permit consumed exactly once');
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Only paired initial anonymous session 401s tolerated');
  assertClean();
  console.log('PASS T50: synthetic UI MIET/700+150 lease, bank/export scope, explicit creditor/FRST mandate GET persistence; missing-mandate 422; exact October 850.00 preview; edit invalidation; two local actual XML downloads incl. phone; original ISO+EPC validation; full source DTOs unchanged; NO transmission/real consent/payment/history mutation.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
