#!/usr/bin/env node
/* T39 acceptance, UI mutations ONLY. Root starts isolated dev-local UI13000 /
 * API18000 with temporary document storage after frontend/backend are green.
 * No server, database, seeds, fetch/request client, mocks or workspace artifacts.
 * Run: node scripts/acceptance/t39_property_year_report_browser.cjs
 * Existing UI labels follow T25/T28/T31/T35. NEW T39 assumptions: /berichte,
 * heading Berichte, Objekt *, Jahr *, Übersicht anzeigen, CSV herunterladen;
 * named tables Einnahmen/Ausgaben and a summary definition list;
 * income vocabulary Kaltmiete/NK-Vorauszahlung;
 * CSV payment_type codes, category_name and ORIGINAL apportionability codes.
 * Reload deliberately reselects inputs and submits: no blind report GET allowed.
 * All dates are fixed in January/June 2026. No settlement/coverage plan needed.
 * Only node --check was run by the author; browser PASS requires Root execution.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T39 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `=${prefix} MIET`; // Deliberate spreadsheet-formula text fixture.
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
  let reportPermit = null;
  const reportRequests = [], downloads = [], scopeChoices = [];
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
      if (['/api/reports/property-year', '/api/reports/property-year.csv'].includes(url.pathname)) {
        reportRequests.push(request);
        if (!reportPermit || reportPermit.seen || url.origin !== base
          || url.pathname !== reportPermit.pathname
          || JSON.stringify([...url.searchParams].sort()) !== JSON.stringify(reportPermit.query)) {
          errors.push(`Unexpected report GET: ${request.url()}`);
          return route.abort('blockedbyclient');
        }
        reportPermit.seen = true;
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
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Berichtsweg 39'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T39 synthetische Jahresuebersicht mit ausdruecklichen Quellrechten');
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
    ['Strasse', 'Mieterweg 39'], ['PLZ', '12346'], ['Stadt', 'Mieterstadt']]) await field(page, label).fill(value);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Mieterweg 39', '12346', 'Mieterstadt']);
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
  await field(grantForm, 'Begründung').fill('T39 eigene MIET-Rechte Lesen, Vorbereiten, Bestätigen und CSV-Export; kein Versand');
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
    ['KALTMIETE', 'OUT', '50.00', '2026-06-05'],
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
    const text = `T39 SYNTHETIC Grundsteuer ${number}; invoice 2026-06-15; gross ${amount} EUR`;
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
    for (const [label, value] of [['Rechnungsdatum *', '2026-06-15'], ['Leistungszeitraum von *', '2026-01-01'],
      ['Leistungszeitraum bis *', '2026-06-30'], ['Bruttobetrag *', amount], ['USt-Betrag', '0.00'],
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
  const writesBeforeReports = actualWrites.length;
  const query = Object.entries({ property_id: property.id, year: '2026', accounting_scope_id: scope.id }).sort();
  async function reportRead(pathname, action) {
    assert.equal(reportPermit, null);
    const permit = { pathname, query, seen: false };
    reportPermit = permit;
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi('GET', pathname)), action()]);
      assert.equal(permit.seen, true);
      assert.deepEqual([...new URL(response.url()).searchParams].sort(), query, 'Exact complete query, no duplicate/extra parameters');
      assert.equal(response.status(), 200, `Report GET failed: ${response.url()}`);
      assertClean();
      return response;
    } finally { reportPermit = null; }
  }
  function money(text) {
    const value = text.trim().replace(/\s*(?:EUR|€)$/, '').replace(/[\s\u00a0\u202f]/g, '');
    if (/^-?\d+\.\d{2}$/.test(value)) return value;
    assert.match(value, /^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}$/, `Unexpected UI money: ${text}`);
    return value.replace(/\./g, '').replace(',', '.');
  }
  function checkReport(report) {
    assert.equal(report.property_id, property.id);
    assert.equal(report.property_name, propertyName);
    assert.equal(report.year, 2026);
    assert.equal(report.accounting_scope_id, scope.id);
    assert.equal(report.period_start, '2026-01-01');
    assert.equal(report.period_end, '2026-12-31');
    assert.equal(report.income_date_basis, 'value_date');
    assert.equal(report.expense_date_basis, 'invoice_date');
    assert.equal(report.income_total, '800.00');
    assert.equal(report.expense_total, '125.00');
    assert.equal(report.balance, '675.00');
    assert.deepEqual([...report.income].sort((a, b) => a.payment_type.localeCompare(b.payment_type)), [
      { payment_type: 'KALTMIETE', amount: '650.00' },
      { payment_type: 'NK_VORAUSZAHLUNG', amount: '150.00' },
    ]);
    assert.equal(report.expenses.length, 2);
    for (const cost of costs) {
      const rows = report.expenses.filter(row => row.category_id === cost.cost_category_id
        && row.tenant_apportionability === cost.tenant_apportionable);
      assert.equal(rows.length, 1);
      assert.equal(rows[0].category_code, 'GRUNDSTEUER');
      assert.ok(rows[0].category_name.includes('Grundsteuer'));
      assert.equal(rows[0].amount, cost.gross_amount);
    }
    assert.match(report.footnote, /Wertstellungs.*Rechnungsdatum/);
    assert.match(report.footnote, /keine vollständige Steuererklärung/);
  }
  async function checkReportUI(report) {
    const income = page.getByRole('table', { name: 'Einnahmen', exact: true });
    const expenses = page.getByRole('table', { name: 'Ausgaben', exact: true });
    await income.waitFor();
    await expenses.waitFor();
    await eventually(async () => {
      for (const item of report.income) {
        const row = income.getByRole('row').filter({ has: page.getByRole('cell', { name: vocabulary[item.payment_type], exact: true }) });
        assert.equal(await row.count(), 1);
        const cells = await row.getByRole('cell').allTextContents();
        assert.equal(cells[0], vocabulary[item.payment_type]);
        assert.equal(money(cells[cells.length - 1]), item.amount);
      }
      assert.equal(await income.getByRole('cell', { name: /KALTMIETE|NK_VORAUSZAHLUNG/ }).count(), 0,
        'Payment vocabulary must be human readable');
      for (const item of report.expenses) {
        const row = expenses.getByRole('row').filter({ has: page.getByRole('cell', { name: item.category_name, exact: true }) })
          .filter({ has: page.getByRole('cell', { name: item.tenant_apportionability === 'UMLAGEFAEHIG'
            ? 'Voll umlagefaehig' : 'Nicht umlagefaehig', exact: true }) });
        assert.equal(await row.count(), 1);
        const cells = await row.getByRole('cell').allTextContents();
        assert.equal(money(cells[cells.length - 1]), item.amount);
      }
      const summary = page.getByRole('region', { name: 'Jahresübersicht', exact: true }).locator('dl');
      assert.deepEqual(await summary.locator('dt').allTextContents(), ['Einnahmen', 'Ausgaben', 'Saldo']);
      assert.deepEqual((await summary.locator('dd').allTextContents()).map(money),
        [report.income_total, report.expense_total, report.balance]);
    });
    await page.getByText(report.footnote, { exact: true }).waitFor();
    assertClean();
  }
  async function checkCleared() {
    await eventually(async () => {
      assert.equal(await page.getByRole('table', { name: 'Einnahmen', exact: true }).count(), 0, 'No stale income table');
      assert.equal(await page.getByRole('table', { name: 'Ausgaben', exact: true }).count(), 0, 'No stale expense table');
      assert.equal(await page.getByRole('region', { name: 'Jahresübersicht', exact: true }).count(), 0, 'No stale report or balance');
    });
  }
  async function selectReportInputs() {
    await page.getByRole('heading', { name: 'Berichte', exact: true }).waitFor();
    await selectLabel(page, 'Objekt *', propertyName);
    await field(page, 'Jahr *').fill('2026');
    await selectScope();
    assert.ok(scopeChoices.some(url => url.searchParams.get('capability') === 'read'
      && url.searchParams.get('property_id') === property.id), 'Report selector must query READ scopes bound to this object');
  }
  await page.goto(`${base}/berichte`);
  await selectReportInputs();
  assert.equal(reportRequests.length, 0, 'No initial blind GET, including after input selection');
  const report = await successfulJson(await reportRead('/api/reports/property-year',
    () => button(page, 'Übersicht anzeigen').click()));
  checkReport(report);
  await checkReportUI(report);

  await field(page, 'Jahr *').fill('0');
  await checkCleared();
  const readsBeforeInvalid = reportRequests.length;
  const show = button(page, 'Übersicht anzeigen');
  if (await show.isEnabled()) await show.click();
  else assert.equal(await show.isDisabled(), true);
  await page.waitForTimeout(400);
  assert.equal(reportRequests.length, readsBeforeInvalid, 'Invalid year 0 cannot GET');
  assertClean();
  await field(page, 'Jahr *').fill('2026');
  await checkCleared();
  assert.deepEqual(await successfulJson(await reportRead('/api/reports/property-year', () => show.click())), report);
  await checkReportUI(report);
  await field(page, 'Abrechnungskreis').selectOption('');
  await checkCleared();
  assert.equal(reportRequests.length, 2, 'Scope change must clear, not auto-fetch');
  await selectScope();
  await checkCleared();
  assert.deepEqual(await successfulJson(await reportRead('/api/reports/property-year', () => show.click())), report);
  await checkReportUI(report);
  // Main-frame commit/request-set reload (not timestamps); then fresh selection.
  await freshReload(page, []);
  await selectReportInputs();
  await checkCleared();
  assert.equal(reportRequests.length, 3, 'Reload must not make a blind report GET');
  const reloaded = await successfulJson(await reportRead('/api/reports/property-year', () => show.click()));
  assert.deepEqual(reloaded, report, 'Full report JSON identical after genuine reload and explicit re-selection');
  await checkReportUI(reloaded);

  const downloadEvent = page.waitForEvent('download');
  const csvResponse = await reportRead('/api/reports/property-year.csv', () => button(page, 'CSV herunterladen').click());
  const download = await downloadEvent;
  assert.equal(download.suggestedFilename(), 'objekt-jahresuebersicht-2026.csv');
  assert.match(csvResponse.headers()['content-type'] || '', /^text\/csv(?:;|$)/i);
  assert.match(csvResponse.headers()['content-disposition'] || '', /^attachment\s*;/i);
  assert.match(csvResponse.headers()['content-disposition'], /objekt-jahresuebersicht-2026\.csv/);
  const headers = await csvResponse.request().allHeaders();
  assert.ok(headers.cookie, 'CSV is the real credentialed browser GET');
  const stream = await download.createReadStream();
  assert.ok(stream, 'Actual download artifact must expose bytes');
  const chunks = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  const bytes = Buffer.concat(chunks);
  assert.equal(await download.failure(), null);
  assert.ok(bytes.length > 3);
  // Python sees ONLY the actual downloaded bytes and the observed report DTO.
  // csv + Decimal are standard library; stdin/BytesIO only, no files or DB.
  const parsed = spawnSync('uv', ['run', 'python', '-c', String.raw`
import base64, csv, io, json, re, sys
from decimal import Decimal
payload = json.load(sys.stdin)
raw = base64.b64decode(payload['bytes'], validate=True)
report = payload['report']
assert raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM required'
text = raw[3:].decode('utf-8', errors='strict')
assert text.endswith('\r\n'), 'CSV must terminate with CRLF'
assert '\n' not in text.replace('\r\n', '') and '\r' not in text.replace('\r\n', ''), 'Only CRLF record delimiters'
rows = list(csv.reader(io.StringIO(text, newline=''), delimiter=';', strict=True))
assert rows[0] == ['Objekt','Jahr','Bereich','Zahlungstyp','Kategorie','Umlagefähigkeit','Betrag (EUR)'], rows[0]
assert all(len(row) == 7 for row in rows), rows
# Formula protection belongs to text ONLY; never change the sign of money.
def safe(value):
    value = str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value
def money(value):
    assert re.fullmatch(r'-?\d+\.\d{2}', value), value
    return format(Decimal(value), '.2f').replace('.', ',')
def row(section, payment='', category='', apport='', amount='0.00'):
    return [safe(report['property_name']), str(report['year']), safe(section), safe(payment), safe(category), safe(apport), money(amount)]
expected = [row('Einnahmen', payment=i['payment_type'], amount=i['amount']) for i in report['income']]
expected += [row('Ausgaben', category=e['category_name'], apport=e['tenant_apportionability'], amount=e['amount']) for e in report['expenses']]
expected += [row(label, amount=report[key]) for label, key in [('Einnahmen gesamt','income_total'),('Ausgaben gesamt','expense_total'),('Saldo','balance')]]
assert sorted(rows[1:]) == sorted(expected), (rows[1:], expected)
for item in rows[1:]:
    assert re.fullmatch(r'-?\d+,\d{2}', item[-1]), item
assert [money(report[key]) for key in ('income_total','expense_total','balance')] == ['800,00','125,00','675,00']
assert Decimal(report['income_total']) - Decimal(report['expense_total']) == Decimal(report['balance'])
assert all(item[0].startswith("'=") for item in rows[1:]), 'Actual formula-leading property must be defanged'
print(json.dumps({'rows': len(rows)-1, 'totals': ['800,00','125,00','675,00']}))
`], { cwd: path.resolve(__dirname, '../../backend'),
    input: JSON.stringify({ bytes: bytes.toString('base64'), report }), encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
  assert.ifError(parsed.error);
  assert.equal(parsed.status, 0, parsed.stderr);
  assert.deepEqual(JSON.parse(parsed.stdout), { rows: report.income.length + report.expenses.length + 3,
    totals: ['800,00', '125,00', '675,00'] });
  assert.equal(downloads.length, 1, 'Exactly one actual browser CSV artifact, no PDF export');
  assert.equal(reportRequests.filter(request => new URL(request.url()).pathname.endsWith('.csv')).length, 1);
  assert.equal(reportRequests.length, 5, 'Four explicit JSON reads and one CSV GET');
  assert.deepEqual(await businessSnapshot(), before, 'JSON reads and CSV preserve FULL payment/cost/document/lease/history DTOs');
  assert.equal(actualWrites.length, writesBeforeReports, 'Report, reload and CSV perform no business mutations');
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
  assert.deepEqual([...counts].sort(), [...expectedWrites].sort(), 'Complete enumerated mutation balance, no hidden source/finance writes');
  assert.deepEqual(actualWrites, allowedWrites, 'All UI write permits are single-use');
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Only exact localhost anonymous/logout GET session 401s, console paired 1:1');
  assertClean();
  console.log('PASS T39: actual UI report 2026, 800/125/675, exact credentialed CSV download with BOM/semicolon/CRLF/Decimal and formula protection; complete business DTOs unchanged.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
