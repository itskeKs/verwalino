#!/usr/bin/env node
/* T49 acceptance: synthetic setup and DATEV export through the actual UI only.
 * Requires parent-provided isolated UI13000/API18000 and synthetic login.
 * Never starts servers, uses SQL/API clients, seeds, or writes artifact files.
 * All fixture mutations use enumerated, single-use UI permits from T39.
 * Only node --check was run by the author; browser PASS requires parent execution.
 * Accounts 1111/2222/3333/4444 are synthetic TEST identifiers, NOT tax advice.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T49 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} DATEV MIET`;
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
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], authResponses = [], authConsole = [];
  let authPhase = 'anonymous';
  let pendingWrite = null;
  let exportPermit = null;
  const exportRequests = [], downloads = [], scopeChoices = [];
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
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/reports/')) {
      const permit = exportPermit;
      exportRequests.push(request);
      let matches = permit && !permit.seen && url.origin === base
        && request.method() === permit.method && url.pathname === permit.pathname;
      if (matches && permit.method === 'GET') {
        matches = JSON.stringify([...url.searchParams].sort()) === JSON.stringify(permit.query);
      } else if (matches) {
        try { assert.deepEqual(request.postDataJSON(), permit.body); }
        catch (error) { matches = false; errors.push(`Unexpected DATEV body: ${error.message}`); }
        matches = matches && !url.search;
      }
      if (!matches) {
        errors.push(`Unexpected report request: ${request.method()} ${request.url()}`);
        return route.abort('blockedbyclient');
      }
      permit.seen = true;
      return route.continue(); // Read-only DATEV POSTs are NOT business write permits.
    }
    if (request.method() === 'GET') return route.continue();
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
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Berichtsweg 49'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T49 synthetischer DATEV-Export mit ausdruecklichen Quellrechten');
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
    ['Strasse', 'Mieterweg 49'], ['PLZ', '12346'], ['Stadt', 'Mieterstadt']]) await field(page, label).fill(value);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Mieterweg 49', '12346', 'Mieterstadt']);
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
  await field(grantForm, 'Begründung').fill('T49 eigene MIET-Rechte Lesen, Vorbereiten, Bestätigen und CSV-Export; kein Versand');
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
  await field(page, 'IBAN').fill('DE02120300000000202051');
  await selectScope();
  const { json: bank, payload: bankPayload } = await uiWrite('POST', '/api/bank-accounts',
    () => button(page, 'Speichern').click());
  assert.equal(bank.property_id, property.id);
  assert.equal(bank.account_type, 'LAUFEND');
  assert.equal(bankPayload.accounting_scope_id, scope.id);
  const payments = [];
  const vocabulary = { KALTMIETE: 'Kaltmiete', NK_VORAUSZAHLUNG: 'NK-Vorauszahlung' };
  for (const [type, direction, amount, date] of [
    ['KALTMIETE', 'IN', '700.00', '2026-01-05'],
    ['NK_VORAUSZAHLUNG', 'IN', '150.00', '2026-01-06'],
    ['KALTMIETE', 'OUT', '50.00', '2026-03-05'],
  ]) {
    await page.goto(`${base}/zahlungen`);
    await button(page, '+ Neue Zahlung').click();
    await selectId(page, 'Bankkonto *', bank.id);
    await selectLabel(page, 'Zahlungstyp *', vocabulary[type]);
    await selectLabel(page, 'Richtung *', direction === 'IN' ? 'Eingang (Einzahlung)' : 'Ausgang (Auszahlung)');
    await field(page, 'Betrag *').fill(amount);
    await field(page, 'Wertstellungsdatum *').fill(date);
    await selectId(page, 'Mieter', tenant.id);
    await selectId(page, 'Mietvertrag', lease.id);
    const { json: draft, payload } = await uiWrite('POST', '/api/payments', () => button(page, 'Anlegen').click());
    function checkPayment(value, status) {
      assert.equal(value.bank_account_id, bank.id);
      assert.equal(value.property_id, property.id);
      assert.equal(value.tenant_profile_id, tenant.id);
      assert.equal(value.lease_agreement_id, lease.id);
      assert.equal(value.payment_type, type);
      assert.equal(value.direction, direction);
      assert.equal(value.amount, amount);
      assert.equal(value.value_date, date);
      assert.equal(value.status, status);
      assert.equal(value.reverses_id, null);
      assert.equal(value.reversed_by_id, null);
    }
    assert.equal(payload.payment_type, type);
    checkPayment(draft, 'draft');
    await page.waitForURL(`${base}/zahlungen/${draft.id}`);
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
    const text = `T49 SYNTHETIC Grundsteuer ${number}; invoice 2026-03-15; gross ${amount} EUR`;
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
  for (const [amount, apportionability, humanLabel] of [
    ['100.00', 'UMLAGEFAEHIG', 'Voll umlagefaehig'],
    ['25.00', 'NICHT_UMLAGEFAEHIG', 'Nicht umlagefaehig'],
  ]) {
    const number = `${prefix} ${apportionability}`;
    const filename = `${prefix.replace(/ /g, '-')}-${apportionability}.pdf`;
    await page.goto(`${base}/belege`);
    await field(page, 'Belegdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: receiptPdf(amount, number) });
    const { json: document } = await uiWrite('POST', '/api/invoice-documents/upload',
      () => button(page, 'Hochladen').click());
    assert.equal(document.original_filename, filename);
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) })
      .getByRole('link', { name: 'Details', exact: true }).click();
    await button(page, 'Kostenposition erstellen').click();
    await selectLabel(page, 'Objekt *', propertyName);
    await eventually(async () => {
      const labels = (await field(page, 'Kategorie *').getByRole('option').allTextContents()).map(label => label.trim());
      const matches = labels.filter(label => /\bGrundsteuer\b/.test(label));
      assert.equal(matches.length, 1, `Exactly one canonical Grundsteuer category required: ${JSON.stringify(matches)}`);
      await selectLabel(page, 'Kategorie *', matches[0]);
    });
    for (const [label, value] of [['Rechnungsdatum *', '2026-03-15'], ['Leistungszeitraum von *', '2026-01-01'],
      ['Leistungszeitraum bis *', '2026-03-31'], ['Bruttobetrag *', amount], ['USt-Betrag', '0.00'],
      ['USt-Satz (%)', '0'], ['Belegnummer', number]]) await field(page, label).fill(value);
    await selectLabel(page, 'Umlagefaehigkeit *', humanLabel);
    await selectScope();
    const { json: draft, payload } = await uiWrite('POST', '/api/cost-entries',
      () => button(page, 'Entwurf erstellen').click());
    assert.equal(payload.source_document_id, document.id);
    assert.equal(payload.accounting_scope_id, scope.id);
    assert.equal(payload.tenant_apportionable, apportionability);
    assert.equal(draft.property_id, property.id);
    assert.equal(draft.gross_amount, amount);
    await page.waitForURL(`${base}/kosten/${draft.id}`);
    const { json: confirmed } = await uiWrite('POST', `/api/cost-entries/${draft.id}/confirm`,
      () => button(page, 'Bestaetigen').click(), 200);
    assert.equal(confirmed.id, draft.id);
    assert.equal(confirmed.status, 'confirmed');
    assert.equal(confirmed.gross_amount, amount);
    assert.equal(confirmed.invoice_date, '2026-03-15');
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
  const selection = { property_id: property.id, accounting_scope_id: scope.id,
    period_start: '2026-01-01', period_end: '2026-03-31' };
  const query = Object.entries(selection).sort();
  async function exportRead(method, pathname, body, action) {
    assert.equal(exportPermit, null);
    const permit = { method, pathname, body, query, seen: false };
    exportPermit = permit;
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(permit.seen, true, 'Single-use export permit must be consumed by the actual UI');
      if (response.status() !== 200) throw new Error(`${pathname}: ${response.status()} ${await response.text()}`);
      assert.ok((await response.request().allHeaders()).cookie, 'Real credentialed browser request');
      assertClean();
      return response;
    } finally { exportPermit = null; }
  }
  await page.goto(`${base}/berichte`);
  await page.getByRole('heading', { name: 'Berichte', exact: true }).waitFor();
  await button(page, 'DATEV-Export öffnen').click();
  const ui = page.getByRole('region', { name: 'DATEV-Export', exact: true });
  const previewButton = () => button(ui, 'DATEV-Vorschau erstellen');
  const confirmation = () => ui.getByRole('checkbox', { name: /^Ich habe alle Kontenzuordnungen fachlich geprüft/ });
  const resultRegion = () => ui.getByRole('region', { name: 'Geprüfter DATEV-Buchungsstapel', exact: true });
  for (const label of ['Kontenrahmen *', 'Wirtschaftsjahresbeginn *', 'Beraternummer *',
    'Mandantennummer *', 'Sachkontenlänge *']) {
    assert.equal(await field(ui, label).inputValue(), '', `${label}: no implicit configuration`);
  }
  assert.equal(await confirmation().isChecked(), false);
  assert.equal(await previewButton().isDisabled(), true);
  await selectId(ui, 'DATEV-Objekt *', property.id);
  await field(ui, 'Zeitraum von *').fill('2026-01-01');
  await field(ui, 'Zeitraum bis *').fill('2026-03-31');
  await field(ui, 'Abrechnungskreis suchen').fill(scopeName);
  await selectLabel(ui, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
  assert.equal(await field(ui, 'Abrechnungskreis').inputValue(), scope.id);
  assert.ok(scopeChoices.some(url => url.searchParams.get('capability') === 'export'
    && url.searchParams.get('property_id') === property.id), 'DATEV selector requires object-bound EXPORT scopes');
  assert.equal(exportRequests.length, 0, 'No blind report request on navigation or selection');
  const sources = await successfulJson(await exportRead('GET', '/api/reports/datev/sources', null,
    () => button(ui, 'DATEV-Quellen laden').click()));
  assert.deepEqual(sources.banks, [{ id: bank.id, label: bankName }]);
  assert.deepEqual(sources.payment_types, ['KALTMIETE', 'NK_VORAUSZAHLUNG']);
  assert.equal(sources.categories.length, 1);
  const category = sources.categories[0];
  assert.equal(category.id, costs[0].cost_category_id);
  assert.equal(costs[1].cost_category_id, category.id);
  assert.equal(category.code, 'GRUNDSTEUER');
  assert.match(category.label, /Grundsteuer/);
  equalDecimal(sources.payment_total, '800.00');
  equalDecimal(sources.cost_total, '125.00');

  // Independent fixture ledger: never derive expected accounts/dates/sides from
  // the preview, CSV, or an imported production implementation.
  const expectedRows = [
    { source_type: 'payment', source_id: payments[0].id, day: '2026-01-05', amount: '700.00', side: 'S', account: '1111', counter_account: '2222', text: 'KALTMIETE' },
    { source_type: 'payment', source_id: payments[1].id, day: '2026-01-06', amount: '150.00', side: 'S', account: '1111', counter_account: '2222', text: 'NK_VORAUSZAHLUNG' },
    { source_type: 'payment', source_id: payments[2].id, day: '2026-03-05', amount: '50.00', side: 'H', account: '1111', counter_account: '2222', text: 'KALTMIETE' },
    ...costs.map(cost => ({ source_type: 'cost', source_id: cost.id, day: '2026-03-15', amount: cost.id === costs[0].id ? '100.00' : '25.00', side: 'S', account: '3333', counter_account: '4444', text: 'GRUNDSTEUER' })),
  ];
  const byId = rows => [...rows].sort((a, b) => a.source_id.localeCompare(b.source_id));
  const mappings = [
    [`Bankkonto ${bankName} *`, '1111'],
    ['Zahlungstyp KALTMIETE *', '2222'],
    ['Zahlungstyp NK_VORAUSZAHLUNG *', '2222'],
    [`Kategorie ${category.label} (${category.code}) Konto *`, '3333'],
    [`Kategorie ${category.label} (${category.code}) Gegenkonto *`, '4444'],
  ];
  async function assertUnmapped() {
    for (const [label] of mappings) assert.equal(await field(ui, label).inputValue(), '', `${label}: no default account`);
    assert.equal(await confirmation().isChecked(), false, 'Explicit mapping confirmation required again');
    assert.equal(await previewButton().isDisabled(), true);
    assert.equal(await resultRegion().count(), 0, 'No stale preview/download after chart change');
  }
  await assertUnmapped();
  for (const [label, value] of [['Wirtschaftsjahresbeginn *', '2026-01-01'], ['Beraternummer *', '12345'],
    ['Mandantennummer *', '123'], ['Sachkontenlänge *', '4']]) await field(ui, label).fill(value);
  const digests = [];
  for (const chart of ['SKR03', 'SKR04']) {
    const countBeforeChart = exportRequests.length;
    await selectLabel(ui, 'Kontenrahmen *', chart);
    await assertUnmapped();
    assert.equal(exportRequests.length, countBeforeChart, 'Chart change only invalidates; it never auto-exports');
    for (const [label, value] of mappings) await field(ui, label).fill(value);
    assert.equal(await previewButton().isDisabled(), true, 'Complete mappings alone do not confirm gross accounting');
    await confirmation().check();
    await eventually(async () => assert.equal(await previewButton().isEnabled(), true));
    const config = { ...selection, fiscal_year_start: '2026-01-01', chart,
      advisor_number: '12345', client_number: '123', account_length: 4,
      bank_accounts: { [bank.id]: '1111' }, payment_accounts: { KALTMIETE: '2222', NK_VORAUSZAHLUNG: '2222' },
      category_accounts: { [category.id]: { account: '3333', counter_account: '4444' } }, gross_accounts_reviewed: true };
    const preview = await successfulJson(await exportRead('POST', '/api/reports/datev/preview', config,
      () => previewButton().click()));
    assert.deepEqual(byId(preview.rows), byId(expectedRows), 'Exactly three payment and two cost source postings');
    equalDecimal(preview.payment_total, '800.00');
    equalDecimal(preview.cost_total, '125.00');
    assert.match(preview.source_digest, /^[a-f0-9]{64}$/);
    digests.push(preview.source_digest);
    await ui.getByRole('heading', { name: `DATEV-Vorschau · ${chart}`, exact: true }).waitFor();
    const table = ui.getByRole('table', { name: 'DATEV-Vorschau', exact: true });
    await eventually(async () => {
      const rows = await table.locator('tbody tr').all();
      const cells = await Promise.all(rows.map(row => row.getByRole('cell').allTextContents()));
      const expected = expectedRows.map(row => [row.source_type, row.day, row.amount, row.side,
        row.account, row.counter_account, row.text]);
      assert.deepEqual(cells.map(row => JSON.stringify(row)).sort(), expected.map(row => JSON.stringify(row)).sort());
    });
    await resultRegion().getByText('Zahlungen: 800.00 EUR · Kosten: 125.00 EUR', { exact: true }).waitFor();
    if (chart === 'SKR04') {
      await page.setViewportSize({ width: 390, height: 844 });
      await table.waitFor({ state: 'visible' });
      await button(ui, 'DATEV-Datei herunterladen').scrollIntoViewIfNeeded();
      assert.equal(await button(ui, 'DATEV-Datei herunterladen').isVisible(), true, 'Phone download action remains usable');
    }
    const [download, response] = await Promise.all([
      page.waitForEvent('download'),
      exportRead('POST', '/api/reports/datev.csv', { ...config, expected_source_digest: preview.source_digest },
        () => button(ui, 'DATEV-Datei herunterladen').click()),
    ]);
    assert.equal(download.suggestedFilename(), 'EXTF_Buchungsstapel.csv');
    assert.match(response.headers()['content-type'] || '', /^text\/csv(?:;|$)/i);
    assert.match(response.headers()['content-disposition'] || '', /^attachment\s*;/i);
    assert.match(response.headers()['content-disposition'], /EXTF_Buchungsstapel_2026-01-01_2026-03-31\.csv/);
    assert.equal(response.headers()['x-content-type-options'], 'nosniff');
    assert.equal(response.headers()['cache-control'], 'private, no-store');
    const stream = await download.createReadStream();
    assert.ok(stream, 'Validate actual browser download, not response-only bytes');
    const chunks = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    assert.equal(await download.failure(), null);
    const bytes = Buffer.concat(chunks);
    // Standard-library parser sees actual downloaded bytes and the independent
    // synthetic ledger only. No filesystem artifacts, production imports or DB.
    const parsed = spawnSync('python3', ['-c', String.raw`
import base64, csv, io, json, re, sys
from decimal import Decimal
p = json.load(sys.stdin)
raw = base64.b64decode(p['bytes'], validate=True)
assert raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM'
text = raw[3:].decode('utf-8', errors='strict')
assert text.endswith('\r\n')
assert '\n' not in text.replace('\r\n', '') and '\r' not in text.replace('\r\n', ''), 'CRLF only'
rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=';', strict=True))
assert len(rows) == 7, 'header, column names, exactly five postings'
h = rows[0]
assert len(h) == 31, h
assert h[:5] == ['EXTF', '700', '21', 'Buchungsstapel', '13']
assert re.fullmatch(r'\d{17}', h[5]), h[5]
assert h[6:] == ['', '', 'Hausverwaltung', '', '12345', '123', '20260101', '4', '20260101', '20260331', 'Hausverwaltung Bestandsausgabe', '', '1', '0', '1', 'EUR', '', '', '', '', '03' if p['chart'] == 'SKR03' else '04', '', '', '', ''], h
names = rows[1]
assert len(names) == 125
for index, heading in {0:'Umsatz (ohne Soll/Haben-Kz)', 1:'Soll/Haben-Kennzeichen', 2:'WKZ Umsatz', 6:'Konto', 7:'Gegenkonto (ohne BU-Schlüssel)', 8:'BU-Schlüssel', 9:'Belegdatum', 10:'Belegfeld 1', 13:'Buchungstext', 124:'Abw. Skontokonto'}.items():
    assert names[index] == heading, (index, names[index])
expected = {r['source_id'].replace('-', ''): r for r in p['expected']}
assert len(expected) == 5
seen = set()
payment_net, cost_total = Decimal('0.00'), Decimal('0.00')
for row in rows[2:]:
    assert len(row) == 125
    source = row[10]
    assert source in expected and source not in seen, source
    seen.add(source)
    e = expected[source]
    assert re.fullmatch(r'\d+,\d{2}', row[0]), row[0]
    amount = Decimal(row[0].replace(',', '.'))
    assert amount > 0 and amount == Decimal(e['amount'])
    assert row[1] == e['side'] and row[2] == 'EUR'
    assert row[6:8] == [e['account'], e['counter_account']]
    assert row[8] == '', 'No invented BU/tax key'
    assert row[9] == e['day'][8:10] + e['day'][5:7], 'DDMM including leading zero'
    assert row[13] == e['text']
    assert all(value == '' for i, value in enumerate(row) if i not in {0,1,2,6,7,9,10,13}), 'No implicit tax/extra booking data'
    if e['source_type'] == 'payment':
        payment_net += amount if row[1] == 'S' else -amount
    else:
        cost_total += amount
assert seen == set(expected)
assert payment_net == Decimal('800.00') and cost_total == Decimal('125.00')
print(json.dumps({'postings': 5, 'payment_net': str(payment_net), 'cost_total': str(cost_total)}))
`], { input: JSON.stringify({ bytes: bytes.toString('base64'), chart, expected: expectedRows }),
      encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
    assert.ifError(parsed.error);
    assert.equal(parsed.status, 0, parsed.stderr);
    assert.deepEqual(JSON.parse(parsed.stdout), { postings: 5, payment_net: '800.00', cost_total: '125.00' });
    assertClean();
  }
  assert.notEqual(digests[0], digests[1], 'Preview digest binds the explicitly selected chart');
  await page.setViewportSize({ width: 1440, height: 1000 });
  const sourcesAfter = await successfulJson(await exportRead('GET', '/api/reports/datev/sources', null,
    () => button(ui, 'DATEV-Quellen laden').click()));
  assert.deepEqual(sourcesAfter, sources, 'Source selection DTO unchanged across both exports');
  await assertUnmapped();
  assert.equal(downloads.length, 2, 'Exactly one actual CSV artifact per chart; no receipt/PDF export');
  assert.equal(exportRequests.length, 6, 'Two source GETs, two preview POSTs, two CSV POSTs only');
  assert.deepEqual(await businessSnapshot(), before, 'Full payment/cost/document/lease/history DTOs unchanged');
  assert.equal(actualWrites.length, writesBeforeExports, 'Read-only DATEV POSTs perform no business mutations');
  authPhase = 'logging-out';
  await uiWrite('POST', '/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await button(page, 'Anmelden').waitFor();
  const counts = new Map();
  for (const key of actualWrites) counts.set(key, (counts.get(key) || 0) + 1);
  const expectedWrites = new Map([
    ['POST /api/auth/login', 1], ['POST /api/auth/logout', 1], ['POST /api/properties', 1],
    ['POST /api/units', 1], ['POST /api/persons', 1], ['POST /api/person-data-domains', 1],
    [`POST /api/person-data-domains/${domain.id}/grants`, 1], ['POST /api/tenant-profiles', 1],
    ['POST /api/leases', 1], ['POST /api/accounting-scopes', 1],
    [`POST /api/accounting-scopes/${scope.id}/grants`, 1], ['POST /api/bank-accounts', 1],
    ['POST /api/payments', 3], ...payments.map(value => [`POST /api/payments/${value.id}/book`, 1]),
    ['POST /api/invoice-documents/upload', 2], ['POST /api/cost-entries', 2],
    ...costs.map(value => [`POST /api/cost-entries/${value.id}/confirm`, 1]),
  ]);
  assert.deepEqual([...counts].sort(), [...expectedWrites].sort(), 'Complete enumerated UI mutation balance');
  assert.deepEqual(actualWrites, allowedWrites, 'All mutation permits consumed once');
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Only paired anonymous/logout session 401s tolerated');
  assertClean();
  console.log('PASS T49: actual UI SKR03/SKR04, explicit synthetic accounts, Q1 2026 payment net 800/costs 125; digest-bound real EXTF downloads; Decimal/125 columns/31-field header; full source DTOs unchanged.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
