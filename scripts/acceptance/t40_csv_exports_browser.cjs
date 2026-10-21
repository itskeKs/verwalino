#!/usr/bin/env node
/* T40 acceptance: author runs node --check ONLY, not a browser PASS.
 * Root first makes backend/frontend green, then starts isolated dev-local
 * UI13000/API18000 with temporary document storage; this script starts nothing.
 * Run: node scripts/acceptance/t40_csv_exports_browser.cjs
 * All setup and finance mutations use human UI controls, no API/SQL seeds,
 * request client, response mocks, disk fixtures or report endpoints.
 * Source: T39 UI/source/scope grants and in-memory upload/download patterns;
 * actual PaymentsPage/CostEntriesPage headings and column labels.
 * Required NEW T40 controls (fail explicitly, no fallback): CSV herunterladen,
 * Statusfilter/Bankkontofilter/Zahlungstypfilter and Statusfilter/Objektfilter;
 * accessible OUTPUT Listensaldo / Listensumme Brutto. Exact filter values and
 * CSV headers are the requested contract. No tax certification is asserted.
 * A request gate holds only a real list GET long enough to observe loading;
 * it never supplies or changes response bytes. Python csv/Decimal uses stdin.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T40 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} MIET A`;
const propertyBName = `${prefix} MIET B`;
const formulaNote = `=${prefix} Zahlungsnotiz`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} Mietkreis`;
const firstName = `${prefix} Ada`;
const lastName = 'CSVmieter';
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
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], authResponses = [], authConsole = [];
  let authPhase = 'anonymous';
  let pendingWrite = null;
  let csvPermit = null;
  let listGate = null;
  const csvRequests = [], listRequests = [], downloads = [], scopeChoices = [];
  context.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/accounting-scopes/page') scopeChoices.push(url);
  });
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
    if (request.method() === 'GET') {
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/reports/')) {
        errors.push(`Forbidden report request: ${request.url()}`);
        return route.abort('blockedbyclient');
      }
      if (['/api/payments', '/api/cost-entries'].includes(url.pathname)) {
        listRequests.push(request);
        if (listGate && listGate.matches(request)) {
          const gate = listGate;
          gate.entered();
          await gate.release;
        }
      }
      if (url.pathname.endsWith('.csv')) {
        csvRequests.push(request);
        if (!csvPermit || csvPermit.seen || url.origin !== base
          || url.pathname !== csvPermit.pathname
          || JSON.stringify([...url.searchParams].sort()) !== JSON.stringify(csvPermit.query)) {
          errors.push(`Unexpected CSV GET: ${request.url()}`);
          return route.abort('blockedbyclient');
        }
        csvPermit.seen = true;
      }
      return route.continue();
    }
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
  page.on('download', download => downloads.push(download));
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
      const request = response.request();
      const payload = (request.headers()['content-type'] || '').includes('application/json')
        ? request.postDataJSON() : null;
      return { json, payload };
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
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer CSVweg 40'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T40 synthetischer Listen-CSV-Export mit ausdruecklichen Quellrechten');
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
    ['Strasse', 'Mieterweg 40'], ['PLZ', '12346'], ['Stadt', 'Mieterstadt']]) await field(page, label).fill(value);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Mieterweg 40', '12346', 'Mieterstadt']);
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
  await field(grantForm, 'Begründung').fill('T40 eigene MIET-Rechte Lesen, Vorbereiten, Bestätigen und CSV-Export; kein Versand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id);
  assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);

  const scopeBName = `${prefix} Mietkreis B`;
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyBName], ['Strasse', 'Synthetischer CSVweg 40'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T40 synthetischer Listen-CSV-Export mit ausdruecklichen Quellrechten');
  const { json: propertyB, payload: propertyBPayload } = await uiWrite('POST', '/api/properties',
    () => button(page, 'Speichern').click());
  assert.equal(propertyB.management_type, 'MIET');
  assert.equal(propertyBPayload.data_audience.label, propertyBName);
  assert.equal(propertyBPayload.data_audience.grants.length, 1);
  assert.equal(propertyBPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyBPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await page.goto(`${base}/berechtigungen`);
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyBName);
  await selectLabel(page, 'Objekt', propertyBName);
  await field(page, 'Bezeichnung').fill(scopeBName);
  await selectLabel(page, 'Verwaltungsart', 'MIET');
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scopeB } = await uiWrite('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click());
  assert.equal(scopeB.property_id, propertyB.id);
  assert.equal(scopeB.scope_type, 'MIET');
  assert.equal(scopeB.whole_property, true);
  assert.equal(scopeB.valid_from, '2026-01-01');
  assert.equal(scopeB.valid_to, null);
  await page.getByRole('link', { name: `${scopeBName} – Teamrechte verwalten`, exact: true }).click();
  const grantBForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Exportieren']) await field(grantBForm, label).check();
  for (const label of ['Freigeben', 'Versenden']) await field(grantBForm, label).uncheck();
  await field(grantBForm, 'Begründung').fill('T40 eigene MIET-Rechte Lesen, Vorbereiten, Bestätigen und CSV-Export; kein Versand');
  const { json: grantB, payload: grantBPayload } = await uiWrite('POST', `/api/accounting-scopes/${scopeB.id}/grants`,
    () => button(grantBForm, 'Freigabe erteilen').click());
  assert.equal(grantB.user_id, principal.user_id);
  assert.equal(grantBPayload.user_id, principal.user_id);
  assert.deepEqual([...grantB.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);
  assert.deepEqual([...grantBPayload.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);

  async function selectScope(selectedScope = scope, selectedName = scopeName) {
    await field(page, 'Abrechnungskreis suchen').fill(selectedName);
    await selectLabel(page, 'Abrechnungskreis', `${selectedName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), selectedScope.id);
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
  await field(page, 'IBAN').fill('DE02120300000000202051');
  await selectScope();
  const { json: bank, payload: bankPayload } = await uiWrite('POST', '/api/bank-accounts',
    () => button(page, 'Speichern').click());
  assert.equal(bank.property_id, property.id);
  assert.equal(bank.account_type, 'LAUFEND');
  assert.equal(bankPayload.accounting_scope_id, scope.id);
  await page.goto(`${base}/bankkonten`);
  await button(page, '+ Neues Bankkonto').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectLabel(page, 'Kontotyp *', 'Laufendes Konto');
  const bankBName = `${prefix} Zweites laufendes Konto`;
  await field(page, 'Bezeichnung *').fill(bankBName);
  await field(page, 'IBAN').fill('DE89370400440532013000');
  await selectScope();
  const { json: bankB, payload: bankBPayload } = await uiWrite('POST', '/api/bank-accounts',
    () => button(page, 'Speichern').click());
  assert.equal(bankB.property_id, property.id);
  assert.equal(bankB.account_type, 'LAUFEND');
  assert.equal(bankBPayload.accounting_scope_id, scope.id);
  const payments = [];
  const vocabulary = { KALTMIETE: 'Kaltmiete', NK_VORAUSZAHLUNG: 'NK-Vorauszahlung' };
  for (const [type, direction, amount, date, account, shouldBook] of [
    ['KALTMIETE', 'IN', '700.00', '2026-01-05', bank, true],
    ['NK_VORAUSZAHLUNG', 'IN', '150.00', '2026-01-06', bank, true],
    ['KALTMIETE', 'OUT', '50.00', '2026-06-05', bank, true],
    ['KALTMIETE', 'IN', '17.00', '2026-01-07', bank, false],
    ['KALTMIETE', 'IN', '23.00', '2026-01-08', bankB, true],
  ]) {
    await page.goto(`${base}/zahlungen`);
    await button(page, '+ Neue Zahlung').click();
    await selectId(page, 'Bankkonto *', account.id);
    await selectLabel(page, 'Zahlungstyp *', vocabulary[type]);
    await selectLabel(page, 'Richtung *', direction === 'IN' ? 'Eingang (Einzahlung)' : 'Ausgang (Auszahlung)');
    await field(page, 'Betrag *').fill(amount);
    await field(page, 'Wertstellungsdatum *').fill(date);
    await selectId(page, 'Mieter', tenant.id);
    await selectId(page, 'Mietvertrag', lease.id);
    await field(page, 'Notiz').fill(formulaNote);
    await field(page, 'Periode Jahr').fill('2026');
    await field(page, 'Periode Monat').fill('1');
    const { json: draft, payload } = await uiWrite('POST', '/api/payments', () => button(page, 'Anlegen').click());
    function checkPayment(value, status) {
      assert.equal(value.bank_account_id, account.id);
      assert.equal(value.property_id, property.id);
      assert.equal(value.tenant_profile_id, tenant.id);
      assert.equal(value.lease_agreement_id, lease.id);
      assert.equal(value.payment_type, type);
      assert.equal(value.direction, direction);
      assert.equal(value.amount, amount);
      assert.equal(value.value_date, date);
      assert.equal(value.status, status);
      assert.equal(value.note, formulaNote);
      assert.equal(value.period_year, 2026);
      assert.equal(value.period_month, 1);
      assert.equal(value.owner_profile_id, null);
      assert.equal(value.reverses_id, null);
      assert.equal(value.reversed_by_id, null);
    }
    assert.equal(payload.payment_type, type);
    checkPayment(draft, 'draft');
    await page.waitForURL(`${base}/zahlungen/${draft.id}`);
    if (!shouldBook) {
      payments.push(draft);
      assert.deepEqual(await readDetail(`/api/payments/${draft.id}`, `/zahlungen/${draft.id}`), draft);
      continue;
    }
    await button(page, 'Buchen').waitFor();
    let accepted = false;
    page.once('dialog', async dialog => {
      assert.equal(dialog.type(), 'confirm');
      assert.match(dialog.message(), /^Zahlung buchen\?/);
      accepted = true;
      await dialog.accept();
    });
    const { json: booked } = await uiWrite('POST', `/api/payments/${draft.id}/book`,
      () => button(page, 'Buchen').click(), 200);
    assert.equal(accepted, true);
    assert.equal(booked.id, draft.id);
    assert.ok(booked.booked_at);
    checkPayment(booked, 'booked');
    payments.push(booked);
    assert.deepEqual(await readDetail(`/api/payments/${booked.id}`, `/zahlungen/${booked.id}`), booked);
  }

  // Minimal valid PDF fixtures: original xref offsets and stream byte lengths,
  // created in memory and uploaded through the actual human file control.
  function receiptPdf(amount, number) {
    const text = `T40 SYNTHETIC Grundsteuer ${number}; invoice 2026-06-15; gross ${amount} EUR`;
    const escaped = text.replace(/([\\()])/g, '\\$1');
    const stream = `BT /F1 12 Tf 40 780 Td (${escaped}) Tj ET\n`;
    const objects = ['<< /Type /Catalog /Pages 2 0 R >>',
      '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
      `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`];
    let pdf = '%PDF-1.4\n';
    const offsets = [];
    objects.forEach((object, i) => {
      offsets.push(Buffer.byteLength(pdf));
      pdf += `${i + 1} 0 obj\n${object}\nendobj\n`;
    });
    const xref = Buffer.byteLength(pdf);
    pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    pdf += offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('');
    pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    return Buffer.from(pdf, 'utf8');
  }
  const costs = [], documents = [];
  for (const [amount, apportionability, humanLabel, targetProperty, targetScope, targetScopeName, shouldConfirm] of [
    ['100.00', 'UMLAGEFAEHIG', 'Voll umlagefaehig', property, scope, scopeName, true],
    ['25.00', 'NICHT_UMLAGEFAEHIG', 'Nicht umlagefaehig', property, scope, scopeName, true],
    ['11.00', 'UMLAGEFAEHIG', 'Voll umlagefaehig', property, scope, scopeName, false],
    ['9.00', 'UMLAGEFAEHIG', 'Voll umlagefaehig', propertyB, scopeB, scopeBName, true],
  ]) {
    const number = `=${prefix} Beleg ${amount}`;
    const filename = `${prefix.replace(/ /g, '-')}-${amount}.pdf`;
    await page.goto(`${base}/belege`);
    await field(page, 'Belegdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: receiptPdf(amount, number) });
    const { json: document } = await uiWrite('POST', '/api/invoice-documents/upload',
      () => button(page, 'Hochladen').click());
    assert.equal(document.original_filename, filename);
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) })
      .getByRole('link', { name: 'Details', exact: true }).click();
    await button(page, 'Kostenposition erstellen').click();
    await selectLabel(page, 'Objekt *', targetProperty.name);
    await eventually(async () => {
      const labels = (await field(page, 'Kategorie *').getByRole('option').allTextContents()).map(label => label.trim());
      const matches = labels.filter(label => /\bGrundsteuer\b/.test(label));
      assert.equal(matches.length, 1, `Exactly one canonical Grundsteuer category required: ${JSON.stringify(matches)}`);
      await selectLabel(page, 'Kategorie *', matches[0]);
    });
    for (const [label, value] of [['Rechnungsdatum *', '2026-06-15'], ['Leistungszeitraum von *', '2026-01-01'],
      ['Leistungszeitraum bis *', '2026-06-30'], ['Bruttobetrag *', amount], ['USt-Betrag', '0.00'],
      ['USt-Satz (%)', '0'], ['Belegnummer', number]]) await field(page, label).fill(value);
    await selectLabel(page, 'Umlagefaehigkeit *', humanLabel);
    await selectScope(targetScope, targetScopeName);
    const { json: draft, payload } = await uiWrite('POST', '/api/cost-entries',
      () => button(page, 'Entwurf erstellen').click());
    assert.equal(payload.source_document_id, document.id);
    assert.equal(payload.accounting_scope_id, targetScope.id);
    assert.equal(payload.tenant_apportionable, apportionability);
    assert.equal(draft.property_id, targetProperty.id);
    assert.equal(draft.gross_amount, amount);
    await page.waitForURL(`${base}/kosten/${draft.id}`);
    assert.equal(draft.status, 'draft_extracted');
    assert.equal(draft.document_number, number);
    if (!shouldConfirm) {
      const persisted = await readDetail(`/api/cost-entries/${draft.id}`, `/kosten/${draft.id}`);
      // Numeric(5,2) fixes the scale after insertion; compare its value, not POST's spelling.
      const normalized = value => ({ ...value,
        vat_rate: value.vat_rate === null ? null : decimal(value.vat_rate) });
      assert.deepEqual(normalized(persisted), normalized(draft));
      costs.push(persisted);
      documents.push(await readDetail(`/api/invoice-documents/${document.id}`, `/belege/${document.id}`));
      continue;
    }
    const { json: confirmed } = await uiWrite('POST', `/api/cost-entries/${draft.id}/confirm`,
      () => button(page, 'Bestaetigen').click(), 200);
    assert.equal(confirmed.id, draft.id);
    assert.equal(confirmed.status, 'confirmed');
    assert.equal(confirmed.document_number, number);
    assert.equal(confirmed.gross_amount, amount);
    assert.equal(confirmed.invoice_date, '2026-06-15');
    assert.equal(confirmed.tenant_apportionable, apportionability);
    assert.deepEqual(await readDetail(`/api/cost-entries/${draft.id}`, `/kosten/${draft.id}`), confirmed);
    const released = await readDetail(`/api/invoice-documents/${document.id}`, `/belege/${document.id}`);
    assert.equal(released.cost_entry_id, draft.id);
    assert.ok(released.released_at, 'The real cost confirmation releases its source document');
    costs.push(confirmed);
    documents.push(released);
  }

  async function businessSnapshot() {
    const result = { payments: [], costs: [], documents: [] };
    for (const value of payments) result.payments.push(await readDetail(`/api/payments/${value.id}`, `/zahlungen/${value.id}`));
    for (const value of costs) result.costs.push(await readDetail(`/api/cost-entries/${value.id}`, `/kosten/${value.id}`));
    for (const value of documents) result.documents.push(await readDetail(`/api/invoice-documents/${value.id}`, `/belege/${value.id}`));
    await page.goto(unitUrl);
    const [leases, history] = await freshReload(page, [response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id,
    isApi('GET', `/api/leases/${lease.id}/rent-changes`)]);
    assert.deepEqual(leases.filter(value => value.id === lease.id), [lease]);
    assert.deepEqual(history, []);
    return { ...result, leases, history };
  }
  const before = await businessSnapshot();
  assert.deepEqual(before.payments, payments);
  assert.deepEqual(before.costs, costs);
  assert.deepEqual(before.documents, documents);
  const writesBeforeExports = actualWrites.length;
  const sortDTOs = values => [...values].sort((a, b) => a.id.localeCompare(b.id));
  const queryOf = url => [...new URL(url).searchParams].sort();
  const queryEquals = (url, query) => JSON.stringify(queryOf(url)) === JSON.stringify(Object.entries(query).sort());
  const listResponse = (pathname, query) => response => isApi('GET', pathname)(response) && queryEquals(response.url(), query);
  function money(text) {
    const value = text.trim().replace(/\s*(?:EUR|€)$/, '').replace(/[\s\u00a0\u202f]/g, '');
    if (/^-?\d+\.\d{2}$/.test(value)) return value;
    assert.match(value, /^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}$/, `Unexpected UI money: ${text}`);
    return value.replace(/\./g, '').replace(',', '.');
  }
  async function requiredFilter(name) {
    const control = page.getByRole('combobox', { name, exact: true });
    assert.equal(await control.count(), 1, `T40 UI missing exact filter label: ${name}; no fallback permitted`);
    return control;
  }
  async function checkOutput(name, expected) {
    const output = field(page, name);
    assert.equal(await output.count(), 1, `T40 UI missing accessible output: ${name}`);
    assert.equal(await output.evaluate(node => node.tagName), 'OUTPUT', `${name} must be an output, not guessed table arithmetic`);
    equalDecimal(money(await output.innerText()), expected);
  }
  async function changeFilter(pathname, query, name, value) {
    const control = await requiredFilter(name);
    await eventually(async () => assert.ok((await control.getByRole('option').evaluateAll(
      options => options.map(option => option.value))).includes(value), `${name}: missing option value ${value}`));
    assert.equal(listGate, null);
    let entered, release;
    const enteredPromise = new Promise(resolve => { entered = resolve; });
    const releasePromise = new Promise(resolve => { release = resolve; });
    const started = new Set();
    const onRequest = request => started.add(request);
    page.on('request', onRequest);
    listGate = { matches: request => request.method() === 'GET'
      && new URL(request.url()).pathname === pathname && queryEquals(request.url(), query),
    entered, release: releasePromise };
    const pending = page.waitForResponse(response => started.has(response.request()) && listResponse(pathname, query)(response))
      .then(response => ({ response }), error => ({ error }));
    // Retain a parallel timeout as data until the response waiter is joined.
    const loadingCheck = (async () => {
      await Promise.race([enteredPromise, new Promise((_, reject) => {
        const timer = setTimeout(() => reject(new Error(`No fresh filtered UI GET: ${pathname}`)), 20000);
        enteredPromise.finally(() => clearTimeout(timer));
      })]);
      await page.getByText('Lade...', { exact: true }).waitFor();
      const csv = button(page, 'CSV herunterladen');
      if (await csv.count()) assert.equal(await csv.isDisabled(), true, 'No enabled stale CSV while list is loading');
      assertClean();
    })();
    const csvCount = csvRequests.length;
    try {
      await Promise.all([control.selectOption(value), loadingCheck]);
      assert.equal(csvRequests.length, csvCount, 'Filter changes must not download stale CSV');
    } finally {
      release();
      listGate = null;
      page.off('request', onRequest);
    }
    const result = await pending;
    if (result.error) throw result.error;
    const data = await successfulJson(result.response);
    await page.getByText('Lade...', { exact: true }).waitFor({ state: 'hidden' });
    assert.equal(await button(page, 'CSV herunterladen').isEnabled(), true, 'Settled list has its CSV button');
    assertClean();
    return data;
  }
  // Resolve cells by real source column headings; never infer sums or labels
  // from arbitrary nth-child indices. The Details link proves the DTO row ID.
  async function tableRecords(kind, values, categories = []) {
    const uiPath = kind === 'payments' ? '/zahlungen/' : '/kosten/';
    const table = page.getByRole('table').filter({ has: page.getByRole('columnheader', {
      name: kind === 'payments' ? 'Betrag' : 'Brutto', exact: true }) });
    await table.waitFor();
    await eventually(async () => {
      assert.equal(await table.count(), 1);
      const headings = (await table.getByRole('columnheader').allTextContents()).map(text => text.trim());
      const records = await table.locator('tbody').getByRole('row').evaluateAll((rows, columns) => rows.map(row => {
        const cells = [...row.querySelectorAll('td')];
        const result = Object.fromEntries(columns.map((name, index) => [name, cells[index]?.textContent?.trim()]));
        result.href = row.querySelector('a')?.getAttribute('href');
        return result;
      }), headings);
      assert.equal(records.length, values.length, 'Exactly the currently displayed filtered DTO rows, no hidden fixtures');
      assert.deepEqual(records.map(row => row.href).sort(), values.map(value => uiPath + value.id).sort());
      for (const value of values) {
        const row = records.find(item => item.href === uiPath + value.id);
        assert.equal(row.Datum, kind === 'payments' ? value.value_date : value.invoice_date);
        if (kind === 'payments') {
          assert.equal(row.Typ, vocabulary[value.payment_type]);
          assert.equal(row.Richtung, { IN: 'Eingang', OUT: 'Ausgang' }[value.direction]);
          assert.equal(row.Status, { draft: 'Entwurf', booked: 'Gebucht' }[value.status]);
          assert.equal(row.Gegenpartei, personName);
          assert.equal(row.Konto, `${value.bank_account_id === bank.id ? bankName : bankBName} (${propertyName})`);
          assert.equal(row.Periode, '2026/01');
          equalDecimal(money(row.Betrag), value.amount);
        } else {
          const category = categories.find(item => item.id === value.cost_category_id);
          assert.ok(category, 'Observed category DTO required, no fabricated name');
          assert.equal(row.Kategorie, `${category.code} ${category.label}`);
          assert.equal(row.Objekt, value.property_id === property.id ? propertyName : propertyBName);
          assert.equal(row.Status, { confirmed: 'Bestaetigt', needs_review: 'Pruefung' }[value.status]);
          assert.equal(row.Quelle, { MANUAL: 'Manuell', PDF_IMPORT: 'PDF-Import', E_INVOICE: 'E-Rechnung' }[value.source]);
          equalDecimal(money(row.Brutto), value.gross_amount);
        }
      }
    });
    assertClean();
  }
  async function assertSelectedDTOs(actual, expected) {
    assert.ok(Array.isArray(actual));
    assert.deepEqual(sortDTOs(actual), sortDTOs(expected), 'Full UI XHR DTOs, not only IDs or sums');
  }
  async function downloadCSV(pathname, query, filename) {
    assert.equal(csvPermit, null);
    const permit = { pathname, query: Object.entries(query).sort(), seen: false };
    csvPermit = permit;
    try {
      const event = page.waitForEvent('download');
      const [response, download] = await Promise.all([
        page.waitForResponse(listResponse(pathname, query)), event,
        button(page, 'CSV herunterladen').click(),
      ]);
      assert.equal(permit.seen, true);
      assert.equal(response.status(), 200);
      assert.deepEqual(queryOf(response.url()), permit.query, 'Exact current list filters, no extra/duplicate CSV parameters');
      assert.equal(download.suggestedFilename(), filename);
      assert.match(response.headers()['content-type'] || '', /^text\/csv(?:;|$)/i);
      assert.match(response.headers()['content-disposition'] || '', /^attachment\s*;/i);
      assert.ok(response.headers()['content-disposition'].includes(filename));
      assert.ok((await response.request().allHeaders()).cookie, 'Real credentialed CSV browser GET');
      const stream = await download.createReadStream();
      assert.ok(stream, 'Actual browser download bytes required, no request.get replacement');
      const chunks = [];
      for await (const chunk of stream) chunks.push(Buffer.from(chunk));
      const bytes = Buffer.concat(chunks);
      assert.equal(await download.failure(), null);
      assert.equal(await response.finished(), null, 'Credentialed CSV request must finish without network error');
      // CDP may discard a fetched body; validate the actual downloaded artifact below.
      assertClean();
      return bytes;
    } finally { csvPermit = null; }
  }
  function parseCSV(kind, bytes, dtos) {
    const parsed = spawnSync('uv', ['run', 'python', '-c', String.raw`
import base64, csv, io, json, re, sys
from decimal import Decimal
p = json.load(sys.stdin)
raw = base64.b64decode(p['bytes'], validate=True)
assert raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM required'
text = raw[3:].decode('utf-8', errors='strict')
assert text.endswith('\r\n'), 'Terminating CRLF required'
assert '\n' not in text.replace('\r\n', '') and '\r' not in text.replace('\r\n', ''), 'Only CRLF delimiters'
rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=';', strict=True))
def safe(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
def money(value):
    result = format(Decimal(value), '.2f').replace('.', ',')
    assert re.fullmatch(r'-?\d+,\d{2}', result), result
    return result
def plain(value):
    return '' if value is None else str(value)
if p['kind'] == 'payments':
    header = ['ID','Objekt-ID','Konto-ID','Wertstellung','Richtung','Zahlungsart','Betrag (EUR)','Saldo-Betrag (EUR)','Status','Periodenjahr','Periodenmonat','Mieter-ID','Eigentümer-ID','Vertrag-ID','Notiz']
    expected = []
    for d in p['dtos']:
        amount = Decimal(d['amount'])
        signed = amount if d['direction'] == 'IN' else -amount
        expected.append([safe(d['id']),safe(d['property_id']),safe(d['bank_account_id']),safe(d['value_date']),safe(d['direction']),safe(d['payment_type']),money(amount),money(signed),safe(d['status']),plain(d['period_year']),plain(d['period_month']),safe(d['tenant_profile_id']),safe(d['owner_profile_id']),safe(d['lease_agreement_id']),safe(d['note'])])
    assert all(d['note'].startswith('=T40') for d in p['dtos'])
    amount_col, signed_col, formula_col = 6, 7, 14
    assert sum((Decimal(d['amount']) for d in p['dtos']), Decimal(0)) == Decimal('750.00')
    total = sum((Decimal(r[signed_col].replace(',', '.')) for r in rows[1:]), Decimal(0))
    assert total == Decimal('650.00'), 'Signed balance, NOT positive amount sum 750'
    assert sorted(r[signed_col] for r in rows[1:]) == ['-50,00', '700,00'], 'OUT stays numeric, no apostrophe'
else:
    header = ['ID','Objekt-ID','Rechnungsdatum','Kategorie-ID','Kategorieversion','Brutto (EUR)','Umlagefähigkeit','Status','Belegnummer','Ersetzt durch-ID']
    expected = [[safe(d['id']),safe(d['property_id']),safe(d['invoice_date']),safe(d['cost_category_id']),plain(d['cost_category_plan_version']),money(d['gross_amount']),safe(d['tenant_apportionable']),safe(d['status']),safe(d['document_number']),safe(d['superseded_by_id'])] for d in p['dtos']]
    amount_col, signed_col, formula_col = 5, None, 8
    assert all(d['document_number'].startswith('=T40') for d in p['dtos'])
    total = sum((Decimal(r[amount_col].replace(',', '.')) for r in rows[1:]), Decimal(0))
    assert total == Decimal('125.00')
assert rows[0] == header, rows[0]
assert all(len(r) == len(header) for r in rows)
assert len(rows) == len(p['dtos']) + 1 == 3, 'Exactly two visible rows, no hidden fixtures'
assert sorted(rows[1:]) == sorted(expected), (rows[1:], expected)
assert len({r[0] for r in rows[1:]}) == 2
for r in rows[1:]:
    assert re.fullmatch(r'-?\d+,\d{2}', r[amount_col]), r
    assert r[formula_col].startswith("'=T40"), 'Text formula must be defanged'
print(json.dumps({'rows': len(rows)-1, 'total': format(total, '.2f')}))
`], { cwd: path.resolve(__dirname, '../../backend'),
      input: JSON.stringify({ kind, bytes: bytes.toString('base64'), dtos }), encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ifError(parsed.error);
    assert.equal(parsed.status, 0, parsed.stderr || parsed.stdout);
    assert.deepEqual(JSON.parse(parsed.stdout), { rows: 2, total: kind === 'payments' ? '650.00' : '125.00' });
  }

  await page.goto(`${base}/zahlungen`);
  const [allPayments] = await freshReload(page, [listResponse('/api/payments', {})]);
  for (const value of payments) assert.deepEqual(allPayments.find(item => item.id === value.id), value);
  for (const label of ['Statusfilter', 'Bankkontofilter', 'Zahlungstypfilter']) {
    assert.equal(await (await requiredFilter(label)).inputValue(), '');
  }
  let paymentList = await changeFilter('/api/payments', { status: 'booked' }, 'Statusfilter', 'booked');
  for (const value of payments) assert.equal(paymentList.some(item => item.id === value.id), value.status === 'booked');
  paymentList = await changeFilter('/api/payments', { status: 'booked', bank_account_id: bank.id }, 'Bankkontofilter', bank.id);
  await assertSelectedDTOs(paymentList, payments.filter(value => value.status === 'booked' && value.bank_account_id === bank.id));
  const paymentQuery = { status: 'booked', bank_account_id: bank.id, payment_type: 'KALTMIETE' };
  paymentList = await changeFilter('/api/payments', paymentQuery, 'Zahlungstypfilter', 'KALTMIETE');
  const selectedPayments = payments.filter(value => value.status === 'booked' && value.bank_account_id === bank.id && value.payment_type === 'KALTMIETE');
  await assertSelectedDTOs(paymentList, selectedPayments);
  await tableRecords('payments', paymentList);
  await checkOutput('Listensaldo', '650.00');
  assert.ok(queryEquals(page.url(), paymentQuery));
  const [reloadedPayments] = await freshReload(page, [listResponse('/api/payments', paymentQuery)]);
  await assertSelectedDTOs(reloadedPayments, paymentList);
  assert.ok(queryEquals(page.url(), paymentQuery), 'Reload preserves exact payment URL filters');
  await tableRecords('payments', reloadedPayments);
  await checkOutput('Listensaldo', '650.00');
  for (const [label, value] of [['Statusfilter', 'booked'], ['Bankkontofilter', bank.id], ['Zahlungstypfilter', 'KALTMIETE']]) {
    assert.equal(await (await requiredFilter(label)).inputValue(), value);
  }
  parseCSV('payments', await downloadCSV('/api/payments/export.csv', paymentQuery, 'zahlungen.csv'), reloadedPayments);
  const [paymentsAfter] = await freshReload(page, [listResponse('/api/payments', paymentQuery)]);
  await assertSelectedDTOs(paymentsAfter, reloadedPayments);
  await tableRecords('payments', paymentsAfter);
  await checkOutput('Listensaldo', '650.00');

  await page.goto(`${base}/kosten`);
  const [allCosts, categories] = await freshReload(page, [listResponse('/api/cost-entries', {}), isApi('GET', '/api/cost-categories')]);
  for (const value of costs) assert.deepEqual(allCosts.find(item => item.id === value.id), value);
  for (const label of ['Statusfilter', 'Objektfilter']) assert.equal(await (await requiredFilter(label)).inputValue(), '');
  let costList = await changeFilter('/api/cost-entries', { status: 'confirmed' }, 'Statusfilter', 'confirmed');
  for (const value of costs) assert.equal(costList.some(item => item.id === value.id), value.status === 'confirmed');
  const costQuery = { status: 'confirmed', property_id: property.id };
  costList = await changeFilter('/api/cost-entries', costQuery, 'Objektfilter', property.id);
  const selectedCosts = costs.filter(value => value.status === 'confirmed' && value.property_id === property.id);
  await assertSelectedDTOs(costList, selectedCosts);
  await tableRecords('costs', costList, categories);
  await checkOutput('Listensumme Brutto', '125.00');
  assert.ok(queryEquals(page.url(), costQuery));
  const [reloadedCosts] = await freshReload(page, [listResponse('/api/cost-entries', costQuery)]);
  await assertSelectedDTOs(reloadedCosts, costList);
  assert.ok(queryEquals(page.url(), costQuery), 'Reload preserves exact cost URL filters');
  await tableRecords('costs', reloadedCosts, categories);
  await checkOutput('Listensumme Brutto', '125.00');
  for (const [label, value] of [['Statusfilter', 'confirmed'], ['Objektfilter', property.id]]) {
    assert.equal(await (await requiredFilter(label)).inputValue(), value);
  }
  parseCSV('costs', await downloadCSV('/api/cost-entries/export.csv', costQuery, 'kosten.csv'), reloadedCosts);
  const [costsAfter] = await freshReload(page, [listResponse('/api/cost-entries', costQuery)]);
  await assertSelectedDTOs(costsAfter, reloadedCosts);
  await tableRecords('costs', costsAfter, categories);
  await checkOutput('Listensumme Brutto', '125.00');

  assert.equal(downloads.length, 2, 'Exactly two actual CSV downloads; no report/PDF export');
  assert.equal(csvRequests.length, 2);
  assert.deepEqual(csvRequests.map(request => new URL(request.url()).pathname), ['/api/payments/export.csv', '/api/cost-entries/export.csv']);
  assert.ok(listRequests.some(request => queryEquals(request.url(), paymentQuery)));
  assert.ok(listRequests.some(request => queryEquals(request.url(), costQuery)));
  assert.deepEqual(await businessSnapshot(), before, 'CSV/list/reload preserve full payments, costs, documents, leases and rent history');
  assert.equal(actualWrites.length, writesBeforeExports, 'CSV, list filters and reload never mutate payment/cost/history');
  authPhase = 'logging-out';
  await uiWrite('POST', '/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await button(page, 'Anmelden').waitFor();
  const counts = new Map();
  for (const key of actualWrites) counts.set(key, (counts.get(key) || 0) + 1);
  const expectedWrites = new Map([
    ['POST /api/auth/login', 1], ['POST /api/auth/logout', 1], ['POST /api/properties', 2],
    ['POST /api/units', 1], ['POST /api/persons', 1], ['POST /api/person-data-domains', 1],
    [`POST /api/person-data-domains/${domain.id}/grants`, 1], ['POST /api/tenant-profiles', 1],
    ['POST /api/leases', 1], ['POST /api/accounting-scopes', 2],
    [`POST /api/accounting-scopes/${scope.id}/grants`, 1], [`POST /api/accounting-scopes/${scopeB.id}/grants`, 1],
    ['POST /api/bank-accounts', 2], ['POST /api/payments', 5],
    ...payments.filter(value => value.status === 'booked').map(value => [`POST /api/payments/${value.id}/book`, 1]),
    ['POST /api/invoice-documents/upload', 4], ['POST /api/cost-entries', 4],
    ...costs.filter(value => value.status === 'confirmed').map(value => [`POST /api/cost-entries/${value.id}/confirm`, 1]),
  ]);
  assert.deepEqual([...counts].sort(), [...expectedWrites].sort(), 'Complete enumerated mutation balance');
  assert.deepEqual(actualWrites, allowedWrites, 'All UI write permits are single-use');
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Exact anonymous/logout GET session 401s paired with console 1:1');
  assertClean();
  console.log('PASS T40: UI payment/cost filters and signed balance 650 / gross 125, two real credentialed CSV downloads, exact DTO/CSV contract and unchanged business history.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
