#!/usr/bin/env node
/* T26: UI-only sale on 2026-06-30 and actual WEG annual allocation.
 * (actual costs/completeness). RF does not establish an exercised sale workflow.
 * Root starts scripts/dev-local.sh separately: UI 13000, API 18000, test DB
 * infra-postgres-1:55432 ONLY. No servers/DBs started by this script.
 * Run: node scripts/acceptance/t26_ownership_change_browser.cjs
 * Requires test@example.de/testpassword, existing Chrome and Playwright.
 * All business mutations use UI; JSON/IDs only observe UI-triggered responses.
 * No API/SQL business seeding, mocks, screenshots, reports or workspace PDFs.
 * Synthetic records intentionally remain. A prerequisite zero-budget plan is
 * created AFTER the sale; actual confirmed receipt costs are exactly 365 EUR.
 * Source prerequisite: /belege's file input must have a real accessible label.
 * Missing labels fail explicitly; no CSS/index upload or native-select fallback.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T26 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} WEG`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} WEG-Jahreskreis`;
const receiptNumber = `${prefix} Rechnung`;
const filename = `${prefix.replace(/ /g, '-')}.pdf`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const isApi = (method, path) => response => response.request().method() === method
  && new URL(response.url()).pathname === path;
let browser;

async function responseFor(page, predicate, action) {
  const [response] = await Promise.all([page.waitForResponse(predicate), action()]);
  return response;
}
async function successfulJson(response, status = 200) {
  if (response.status() !== status) assert.fail(
    `${response.request().method()} ${response.url()}: expected ${status}, got ${response.status()}: ${await response.text()}`);
  return response.json();
}
async function reloadJson(page, predicate) {
  let committed = false;
  const freshRequests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const requestStarted = request => { if (committed) freshRequests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', requestStarted);
  try {
    return await successfulJson(await responseFor(page, response => predicate(response)
      && freshRequests.has(response.request()), () => page.reload()));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', requestStarted);
  }
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
async function selectLabel(page, label, optionLabel) {
  const select = field(page, label);
  await select.getByRole('option', { name: optionLabel, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label: optionLabel });
}
async function chooseId(page, label, id) {
  const select = field(page, label);
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${label}: missing observed ID ${id}`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
}
function normalized(text) { return text.trim().replace(/\s+/g, ' '); }
function money(text) {
  const value = text.trim().replace(/\s*(?:EUR|€)$/, '').replace(/[\s\u00a0\u202f]/g, '');
  if (/^-?\d+\.\d{2}$/.test(value)) return value;
  assert.match(value, /^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}$/, `Unknown money: ${text}`);
  return value.replace(/\./g, '').replace(',', '.');
}
function cents(text) {
  const value = money(text);
  return BigInt(value.replace('.', ''));
}
function decimalOne(value) {
  assert.match(value, /^1(?:\.0+)?$/, 'Full ownership/unit MEA must be exactly one');
}
function ownershipValue(value) {
  assert.ok(value, 'Expected ownership row exists');
  if (value.mea_share === null) return value;
  assert.match(value.mea_share, /^\d+(?:\.\d+)?$/);
  const [whole, fraction = ''] = value.mea_share.split('.');
  const digits = fraction.replace(/0+$/, '');
  return { ...value, mea_share: whole + (digits ? `.${digits}` : '') };
}
// A complete, uncompressed, single-page PDF; offsets and stream length are byte
// counts, not guessed constants. No external renderer or fixture file required.
function receiptPdf() {
  const text = [
    'T26 SYNTHETIC INVOICE - NOT A REAL CLAIM',
    `Receipt ${receiptNumber}`,
    'Supplier: Synthetic WEG Services',
    'Invoice date: 2026-12-15',
    'Service period: 2026-01-01 to 2026-12-31',
    'WEG administration: 365.00 EUR',
    'VAT: 0.00 EUR; gross total: 365.00 EUR',
  ];
  const escape = value => value.replace(/([\\()])/g, '\\$1');
  const stream = `BT /F1 12 Tf 50 780 Td 18 TL\n${text.map((line, index) => `${index ? 'T* ' : ''}(${escape(line)}) Tj`).join('\n')}\nET\n`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf);
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], mutations = [], expectedAuth401 = [], consoleAuth401 = [];
  let authPhase = 'anonymous';
  const sessions = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) consoleAuth401.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') mutations.push(request);
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) {
      expectedAuth401.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  function assertClean() { assert.deepEqual(errors, [], 'No unexpected HTTP/page/console errors'); }
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  async function observe(method, path, action, status = 200) {
    const response = await responseFor(page, isApi(method, path), action);
    const json = await successfulJson(response, status);
    assertClean();
    return { response, json };
  }
  async function selectProperty(label) {
    const group = page.getByRole('group', { name: label, exact: true });
    await field(group, 'Objekte durchsuchen').fill(propertyName);
    await selectLabel(page, label, propertyName);
  }
  async function selectScope(scopeId) {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · WEG · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scopeId);
  }
  async function confirmAction(message, action) {
    const dialogPromise = page.waitForEvent('dialog');
    const clickPromise = action();
    const dialog = await dialogPromise;
    assert.equal(dialog.type(), 'confirm');
    assert.equal(dialog.message(), message);
    await dialog.accept();
    await clickPromise;
  }
  async function coverage(kind, id, reason, receipt = null) {
    const section = page.getByRole('region', { name: 'Datengrundlage bestätigen', exact: true });
    await field(section, 'Grundlage der Prüfung').fill(reason);
    if (receipt) {
      const { json } = await observe('GET', '/api/financial-input-coverage/source-options',
        () => button(section, 'Freigegebene Belege auswählen').click());
      assert.equal(json.next_cursor, null, 'Fresh scope receipt options fit one page');
      const candidates = json.items.filter(item => item.id === receipt.id);
      assert.equal(candidates.length, 1);
      await field(section, `${receiptNumber} · Version ${receipt.version}`).check();
    }
    await section.getByRole('checkbox', {
      name: 'Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge.', exact: true,
    }).check();
    const { response } = await observe('POST', '/api/financial-input-coverage',
      () => button(section, 'Datengrundlage speichern').click(), 201);
    const payload = response.request().postDataJSON();
    assert.equal(payload.subject_kind, kind);
    assert.equal(payload.subject_id, id);
    assert.equal(payload.confirmed_complete, true);
    assert.deepEqual(payload.source_document_ids, receipt ? [receipt.id] : []);
    await section.getByRole('status').filter({ hasText: 'Prüfung gespeichert.' }).waitFor();
  }

  // Preserve /objekte as the post-login destination; never visit the dashboard.
  await page.goto(base + '/objekte');
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await observe('POST', '/api/auth/login', () => button(page, 'Anmelden').click());
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();

  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(propertyName);
  await field(page, 'Verwaltungsart *').selectOption({ label: 'WEG' });
  await field(page, 'Strasse').fill('Synthetischer Eigentumsweg 26');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await field(page, 'Begründung der Freigabe').fill('T26 synthetischer WEG-Verkauf im isolierten Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'WEG');
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  await field(page, 'Nenner *').fill('1');
  await button(page, 'MEA-Skala bestätigen').click();
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  await field(page, 'MEA (Miteigentumsanteil)').fill('1');
  const { json: unit } = await observe('POST', '/api/units', () => button(page, 'Speichern').click(), 201);
  assert.equal(unit.property_id, property.id);
  decimalOne(unit.mea);
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitUrl = page.url();
  assert.ok(new URL(unitUrl).pathname.endsWith(`/${unit.id}`));

  async function createOwner(role) {
    const firstName = `${prefix} ${role}`;
    const name = `${firstName} Synthetisch`;
    await page.goto(base + '/personen');
    await button(page, '+ Neue Person').click();
    await field(page, 'Vorname').fill(firstName);
    await field(page, 'Nachname *').fill('Synthetisch');
    const { json: person } = await observe('POST', '/api/persons', () => button(page, 'Speichern').click(), 201);
    await searchList(page, 'Personen suchen');
    await page.getByRole('link', { name, exact: true }).click();
    const { json: owner } = await observe('POST', '/api/owner-profiles',
      () => button(page, 'Als Eigentuemer kennzeichnen').click(), 201);
    assert.equal(owner.person_id, person.id);
    await page.getByText('Eigentuemer', { exact: true }).waitFor();
    return { ...owner, name };
  }
  const seller = await createOwner('Seller');
  const buyer = await createOwner('Buyer');
  await page.goto(unitUrl);
  await button(page, '+ Eigentum').click();
  await selectLabel(page, 'Eigentuemer *', seller.name);
  await field(page, 'Gueltig ab *').fill('2026-01-01');
  await field(page, 'Gueltig bis').fill('');
  await field(page, 'MEA-Anteil').fill('1');
  const { json: original } = await observe('POST', '/api/ownership', () => button(page, 'Speichern').click(), 201);
  assert.equal(original.owner_profile_id, seller.id);
  assert.equal(original.unit_id, unit.id);
  assert.equal(original.valid_from, '2026-01-01');
  assert.equal(original.valid_to, null);
  decimalOne(original.mea_share);
  const ownershipCount = () => mutations.filter(request => request.method() === 'POST'
    && new URL(request.url()).pathname === '/api/ownership').length;
  const ownerRow = owner => page.getByRole('table').filter({
    has: page.getByRole('columnheader', { name: 'MEA-Anteil', exact: true }),
  }).getByRole('row').filter({ has: page.getByRole('cell', { name: owner.name, exact: true }) });

  // Cancel first: neither closing the dialog nor selecting a date may mutate.
  await button(ownerRow(seller), 'Eigentum beenden').click();
  const endDialog = page.getByRole('dialog', { name: 'Eigentum beenden', exact: true });
  await endDialog.waitFor();
  await field(endDialog, 'Letzter Eigentumstag').fill('2026-06-30');
  await button(endDialog, 'Abbrechen').click();
  await endDialog.waitFor({ state: 'hidden' });
  assert.equal(mutations.filter(request => request.method() === 'PATCH'
    && new URL(request.url()).pathname === `/api/ownership/${original.id}`).length, 0);
  await ownerRow(seller).getByRole('cell', { name: 'offen', exact: true }).waitFor();
  await button(ownerRow(seller), 'Eigentum beenden').click();
  await field(endDialog, 'Letzter Eigentumstag').fill('2026-06-30');
  const { response: endResponse, json: ended } = await observe('PATCH', `/api/ownership/${original.id}`,
    () => button(endDialog, 'Eigentum beenden').click());
  assert.deepEqual(endResponse.request().postDataJSON(), { valid_to: '2026-06-30' });
  assert.deepEqual(ownershipValue(ended), ownershipValue({ ...original, valid_to: '2026-06-30' }));
  await ownerRow(seller).getByRole('cell', { name: '2026-06-30', exact: true }).waitFor();
  assert.equal(await button(ownerRow(seller), 'Eigentum beenden').count(), 0);
  await page.getByRole('heading', { name: 'Neues Eigentum anlegen', exact: true }).waitFor();
  assert.equal(await field(page, 'Gueltig ab *').inputValue(), '2026-07-01');
  assert.equal(ownershipCount(), 1, 'Ending ownership must not automatically create its successor');
  await selectLabel(page, 'Eigentuemer *', buyer.name);
  await field(page, 'Gueltig bis').fill('');
  await field(page, 'MEA-Anteil').fill('1');
  assert.equal(ownershipCount(), 1, 'Selecting Buyer is not consent to save');
  const { json: successor } = await observe('POST', '/api/ownership', () => button(page, 'Speichern').click(), 201);
  assert.equal(successor.owner_profile_id, buyer.id);
  assert.equal(successor.unit_id, unit.id);
  assert.equal(successor.valid_from, '2026-07-01');
  assert.equal(successor.valid_to, null);
  decimalOne(successor.mea_share);
  async function persistedOwnership() {
    const records = await reloadJson(page, response => isApi('GET', '/api/ownership')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id);
    assert.equal(records.length, 2, 'Only this fresh unit has two synthetic history rows; no global single-owner policy');
    assert.deepEqual(ownershipValue(records.find(record => record.id === original.id)), ownershipValue(ended));
    assert.deepEqual(ownershipValue(records.find(record => record.id === successor.id)), ownershipValue(successor));
    for (const [date, expectedOwner] of [['2026-06-30', seller.id], ['2026-07-01', buyer.id]]) {
      const active = records.filter(record => record.valid_from <= date && (!record.valid_to || record.valid_to >= date));
      assert.deepEqual(active.map(record => record.owner_profile_id), [expectedOwner], `No gap/overlap on ${date} in this fixture`);
    }
    for (const [owner, start, end] of [[seller, '2026-01-01', '2026-06-30'], [buyer, '2026-07-01', 'offen']]) {
      await ownerRow(owner).getByRole('cell', { name: start, exact: true }).waitFor();
      await ownerRow(owner).getByRole('cell', { name: end, exact: true }).waitFor();
    }
    await button(ownerRow(buyer), 'Eigentum beenden').waitFor();
    assertClean();
  }
  await persistedOwnership();

  await page.goto(base + '/berechtigungen/teamrechte');
  await selectProperty('Objekt');
  await field(page, 'Bezeichnung').fill(scopeName);
  await field(page, 'Verwaltungsart').selectOption({ label: 'WEG' });
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await observe('POST', '/api/accounting-scopes', () => button(page, 'Bereich verbindlich anlegen').click(), 201);
  assert.equal(scope.property_id, property.id);
  assert.equal(scope.scope_type, 'WEG');
  assert.equal(scope.valid_from, '2026-01-01');
  assert.equal(scope.valid_to, null);
  assert.equal(scope.whole_property, true);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await chooseId(page, 'Teammitglied', principal.user_id);
  const member = await field(page, 'Teammitglied').getByRole('option', { selected: true }).textContent();
  assert.ok(member.includes('test@example.de'));
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Freigeben', 'Exportieren']) await field(grantForm, label).check();
  await field(grantForm, 'Versenden').uncheck();
  await field(grantForm, 'Begründung').fill('T26 ausdrückliche eigene WEG-Finanzrechte, keine Admin-Ersatzberechtigung');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'confirm', 'export', 'prepare', 'read']);

  // Empty positions are allowed by the existing plan UI/engine. The plan is
  // merely the Soll prerequisite; it is not substituted for actual year costs.
  await page.goto(base + '/wirtschaftsplaene');
  await button(page, '+ Neuer Wirtschaftsplan').click();
  await selectLabel(page, 'WEG-Objekt *', `${propertyName} (WEG)`);
  await field(page, 'Wirtschaftsjahr *').fill('2026');
  await field(page, 'Zeitraum von *').fill('2026-01-01');
  await field(page, 'Zeitraum bis *').fill('2026-12-31');
  await field(page, 'Rücklagenzuführung (Jahr)').fill('0.00');
  await selectScope(scope.id);
  const { response: planCreateResponse, json: plan } = await observe('POST', '/api/economic-plans', () => button(page, 'Anlegen').click(), 201);
  assert.equal(planCreateResponse.request().postDataJSON().accounting_scope_id, scope.id);
  assert.equal(plan.property_id, property.id);
  await page.waitForURL(base + `/wirtschaftsplaene/${plan.id}`);
  await field(page, 'Rücklagenzuführung (Jahr)').fill('0.00');
  await coverage('economic_plan', plan.id, 'T26 geprüfter synthetischer Nullplan: keine Planpositionen, keine Rücklagenzuführung; keine Zahlungen. Eigentumswechsel bereits gespeichert. Tatsächliche Jahreskosten separat 365 EUR.');
  const { response: planComputeResponse } = await observe('POST', `/api/economic-plans/${plan.id}/compute`, () => button(page, 'Berechnen').click());
  assert.deepEqual(planComputeResponse.request().postDataJSON(), { planned_costs: [], reserve_contribution_total: '0.00' });
  const { json: finalPlan } = await observe('POST', `/api/economic-plans/${plan.id}/finalize`,
    () => confirmAction('Wirtschaftsplan finalisieren? Danach unveränderbar (GoBD); Änderung nur als Korrektur.', () => button(page, 'Finalisieren').click()));
  assert.equal(finalPlan.status, 'finalized');
  assert.equal(finalPlan.reserve_contribution_total, '0.00');
  for (const line of finalPlan.lines) assert.equal(cents(line.planned_amount), 0n);
  const planOwnerIds = [...new Set(finalPlan.lines.map(line => line.owner_profile_id).filter(Boolean))];
  assert.ok(planOwnerIds.includes(seller.id));
  assert.ok(planOwnerIds.every(id => [seller.id, buyer.id].includes(id)));
  const annualOwnerIds = [seller.id, buyer.id];

  await page.goto(base + '/belege');
  await page.getByRole('heading', { name: 'Beleg hochladen', exact: true }).waitFor();
  // Inspect the known source element ONLY to demand a genuine accessible label;
  // never upload via its CSS locator. Root must fix the component + tests if absent.
  const upload = page.locator('input[type="file"]');
  assert.equal(await upload.count(), 1, 'Exactly one upload field on /belege');
  const uploadLabel = await upload.evaluate(input => input.getAttribute('aria-label')
    || [...input.labels].map(label => label.textContent.trim()).filter(Boolean).join(' '));
  assert.ok(uploadLabel, 'UI prerequisite missing: InvoiceDocumentsPage.tsx file input has no accessible label. Add a real label with UI tests; no CSS upload fallback permitted.');
  await field(page, uploadLabel).setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: receiptPdf() });
  const { json: document } = await observe('POST', '/api/invoice-documents/upload', () => button(page, 'Hochladen').click(), 201);
  assert.equal(document.original_filename, filename);
  const documentRow = page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) });
  await documentRow.getByRole('link', { name: 'Details', exact: true }).click();
  await button(page, 'Kostenposition erstellen').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectLabel(page, 'Kategorie *', 'WEG_VERWALTERKOSTEN WEG-Verwalterkosten');
  await field(page, 'Rechnungsdatum *').fill('2026-12-15');
  await field(page, 'Leistungszeitraum von *').fill('2026-01-01');
  await field(page, 'Leistungszeitraum bis *').fill('2026-12-31');
  await field(page, 'Bruttobetrag *').fill('365.00');
  await field(page, 'USt-Betrag').fill('0.00');
  await field(page, 'USt-Satz (%)').fill('0');
  await field(page, 'Belegnummer').fill(receiptNumber);
  await selectLabel(page, 'Umlagefaehigkeit *', 'Nicht umlagefaehig');
  await selectScope(scope.id);
  const { response: costResponse, json: cost } = await observe('POST', '/api/cost-entries', () => button(page, 'Entwurf erstellen').click(), 201);
  assert.equal(costResponse.request().postDataJSON().source_document_id, document.id);
  assert.equal(costResponse.request().postDataJSON().accounting_scope_id, scope.id);
  assert.equal(cost.property_id, property.id);
  assert.equal(cost.owner_distribution_key, 'MEA');
  assert.equal(cost.gross_amount, '365.00');
  assert.equal(cost.service_period_from, '2026-01-01');
  assert.equal(cost.service_period_to, '2026-12-31');
  await page.waitForURL(base + `/kosten/${cost.id}`);
  const { json: confirmedCost } = await observe('POST', `/api/cost-entries/${cost.id}/confirm`, () => button(page, 'Bestaetigen').click());
  assert.equal(confirmedCost.status, 'confirmed');
  assert.equal(confirmedCost.gross_amount, '365.00');
  const { json: released } = await observe('GET', `/api/invoice-documents/${document.id}`,
    () => page.goto(base + `/belege/${document.id}`));
  assert.equal(released.cost_entry_id, cost.id);
  assert.ok(released.released_at, 'Confirmed cost must release its original receipt');

  await page.goto(base + '/weg-abrechnungen');
  await button(page, '+ Neue WEG-Abrechnung').click();
  await selectLabel(page, 'WEG-Objekt *', `${propertyName} (WEG)`);
  await chooseId(page, 'Wirtschaftsplan (Soll) *', plan.id);
  await field(page, 'Zeitraum von *').fill('2026-01-01');
  await field(page, 'Zeitraum bis *').fill('2026-12-31');
  const { json: run } = await observe('POST', '/api/weg-settlements', () => button(page, 'Anlegen').click(), 201);
  assert.equal(run.economic_plan_id, plan.id);
  assert.equal(run.property_id, property.id);
  await page.waitForURL(base + `/weg-abrechnungen/${run.id}`);
  for (const id of annualOwnerIds) {
    const owner = id === seller.id ? seller : buyer;
    const input = field(page, `Tatsächlich gezahltes Hausgeld · ${owner.name}`);
    assert.equal(await input.inputValue(), '', 'Owner payments must initially remain unknown');
    await input.fill('0.00');
  }
  await field(page, 'Rücklagen-Anfangsbestand').fill('0.00');
  await field(page, 'Rücklagen-Entnahmen (Jahr)').fill('0.00');
  await coverage('settlement', run.id, 'T26 vollständiges synthetisches Jahr 2026: genau eine bestätigte beleggebundene WEG-Verwaltungskostenposition 365 EUR, keine Hausgeldzahlungen, Rücklage Anfang/Entnahmen null; Seller bis 30.06. 181 Tage, Buyer ab 01.07. 184 Tage.', released);
  const { response: computeResponse, json: computed } = await observe('POST', `/api/weg-settlements/${run.id}/compute`, () => button(page, 'Berechnen').click());
  const computePayload = computeResponse.request().postDataJSON();
  assert.equal(computePayload.reserve_opening_balance, '0.00');
  assert.equal(computePayload.reserve_withdrawals_total, '0.00');
  assert.deepEqual(computePayload.paid_advances.map(item => item.owner_profile_id).sort(), [...annualOwnerIds].sort());
  assert.ok(computePayload.paid_advances.every(item => item.paid_total === '0.00'));

  function assertAnnual(value) {
    assert.equal(value.id, run.id);
    assert.equal(value.property_id, property.id);
    assert.equal(value.economic_plan_id, plan.id);
    assert.equal(value.run_type, 'WEG_JAHRESABRECHNUNG');
    assert.equal(value.period_start, '2026-01-01');
    assert.equal(value.period_end, '2026-12-31');
    assert.equal(value.total_cost, '365.00');
    const costs = value.lines.filter(line => line.line_type === 'COST');
    assert.equal(costs.length, 2, 'One actual cost split between exactly the two fixture owners');
    assert.equal(costs.reduce((sum, line) => sum + cents(line.amount), 0n), 36500n);
    for (const [owner, days, amount] of [[seller, 181, '181.00'], [buyer, 184, '184.00']]) {
      const ownerCosts = costs.filter(line => line.addressee_owner_profile_id === owner.id);
      assert.equal(ownerCosts.length, 1);
      assert.equal(ownerCosts[0].cost_category_code, 'WEG_VERWALTERKOSTEN');
      assert.equal(ownerCosts[0].distribution_key, 'MEA');
      assert.equal(ownerCosts[0].time_share_days, days);
      assert.equal(ownerCosts[0].time_share_total_days, 365);
      assert.equal(ownerCosts[0].total_amount, '365.00');
      assert.equal(ownerCosts[0].amount, amount);
      const totals = value.lines.filter(line => line.line_type === 'OWNER_COST_TOTAL'
        && line.addressee_owner_profile_id === owner.id);
      assert.equal(totals.length, 1);
      assert.equal(totals[0].amount, amount);
    }
  }
  async function assertAnnualUI() {
    const summary = page.getByRole('table').filter({ has: page.getByRole('cell', { name: 'Gesamtkosten (Ist, ohne Rücklage)', exact: true }) });
    const total = summary.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Gesamtkosten (Ist, ohne Rücklage)', exact: true }) });
    await eventually(async () => assert.equal(money(await total.getByRole('cell').last().innerText()), '365.00'));
    for (const [owner, amount] of [[seller, '181.00'], [buyer, '184.00']]) {
      const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: owner.name, exact: true }) });
      await card.waitFor();
      const table = card.getByRole('table');
      assert.equal(await table.count(), 1);
      for (const label of ['WEG-Verwalterkosten', 'Summe Ist-Kosten (Ihr Anteil)']) {
        const row = table.getByRole('row').filter({ has: page.getByRole('cell', { name: new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\s|$)`) }) });
        await row.waitFor();
        assert.equal(await row.count(), 1);
        const cells = (await row.getByRole('cell').allTextContents()).map(normalized);
        assert.ok(cells.length >= 2, 'Financial table must contain a distinct amount cell');
        assert.equal(money(cells[cells.length - 1]), amount, `${owner.name}: ${label}`);
      }
    }
    assertClean();
  }
  const lineProjection = value => value.lines.map(({ id, ...line }) => line)
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  assertAnnual(computed);
  await assertAnnualUI();
  const persisted = await reloadJson(page, isApi('GET', `/api/weg-settlements/${run.id}`));
  assertAnnual(persisted);
  assert.deepEqual(lineProjection(persisted), lineProjection(computed));
  await assertAnnualUI();
  // Reload intentionally clears unknown financial inputs. Re-enter and re-review
  // them before finalization; never silently reuse an old completeness assertion.
  for (const id of annualOwnerIds) {
    const input = field(page, `Tatsächlich gezahltes Hausgeld · ${(id === seller.id ? seller : buyer).name}`);
    assert.equal(await input.inputValue(), '', 'Reload must not silently reuse an earlier payment declaration');
    await input.fill('0.00');
  }
  await field(page, 'Rücklagen-Anfangsbestand').fill('0.00');
  await field(page, 'Rücklagen-Entnahmen (Jahr)').fill('0.00');
  await coverage('settlement', run.id, 'T26 erneute vollständige Prüfung nach Reload: 365 EUR bestätigte Belegkosten, keine Zahlungen, Nullrücklage; unverändert 181/184 Tage.', released);
  const { json: finalized } = await observe('POST', `/api/weg-settlements/${run.id}/finalize`,
    () => confirmAction('WEG-Jahresabrechnung finalisieren? Danach unveränderbar (GoBD).', () => button(page, 'Finalisieren').click()));
  assert.equal(finalized.status, 'finalized');
  assertAnnual(finalized);
  assert.deepEqual(lineProjection(finalized), lineProjection(computed));
  assert.ok(finalized.snapshot_sha256);
  const finalReload = await reloadJson(page, isApi('GET', `/api/weg-settlements/${run.id}`));
  assert.equal(finalReload.status, 'finalized');
  assert.equal(finalReload.snapshot_sha256, finalized.snapshot_sha256);
  assertAnnual(finalReload);
  assert.deepEqual(lineProjection(finalReload), lineProjection(finalized));
  await page.getByRole('heading', { name: 'WEG-Jahresabrechnung Finalisiert', exact: true }).waitFor();
  await assertAnnualUI();
  const [pdfPage, pdfResponse] = await Promise.all([
    page.waitForEvent('popup'),
    context.waitForEvent('response', { predicate: isApi('GET', `/api/weg-settlements/${run.id}/pdf`) }),
    button(page, 'PDF herunterladen').click(),
  ]);
  assert.equal(pdfResponse.status(), 200);
  assert.ok(pdfResponse.headers()['content-type'].startsWith('application/pdf'));
  await pdfPage.waitForLoadState('load');
  assert.equal(new URL(pdfPage.url()).pathname, `/api/weg-settlements/${run.id}/pdf`);
  assert.equal(await pdfPage.evaluate(() => document.contentType), 'application/pdf');
  const viewerUrl = 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html';
  await eventually(async () => assert.ok(pdfPage.frames().some(frame => frame.url() === viewerUrl), 'Chrome PDF viewer frame must load'));
  const viewerFrame = pdfPage.frames().find(frame => frame.url() === viewerUrl);
  await viewerFrame.locator('pdf-viewer#viewer').waitFor({ state: 'attached' });
  // Chrome exposes its PDF-viewer HTML through CDP rather than the raw stream.
  // Read only the exact document URL already opened by the UI, with its session.
  const rawPdf = await context.request.get(pdfResponse.url());
  assert.equal(rawPdf.status(), 200);
  assert.ok(rawPdf.headers()['content-type'].startsWith('application/pdf'));
  const pdfBytes = await rawPdf.body();
  assert.equal(pdfBytes.subarray(0, 5).toString(), '%PDF-');
  assert.ok(pdfBytes.length < 25 * 1024 * 1024);
  const parsedPdf = spawnSync('uv', ['run', 'python', '-c',
    'import io,json,sys,pdfplumber\nwith pdfplumber.open(io.BytesIO(sys.stdin.buffer.read())) as pdf:\n print(json.dumps([page.extract_text() or "" for page in pdf.pages]))'],
  { cwd: path.resolve(__dirname, '../../backend'), input: pdfBytes, encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  assert.ifError(parsedPdf.error);
  assert.equal(parsedPdf.status, 0, parsedPdf.stderr);
  const pdfPages = JSON.parse(parsedPdf.stdout);
  assert.ok(pdfPages.length > 0, 'The exported PDF must contain readable pages');
  const pdfText = normalized(pdfPages.join(' '));
  for (const owner of [seller, buyer]) assert.ok(pdfText.includes(owner.name), `PDF owner missing: ${owner.name}`);
  assert.match(pdfText, /181[.,]00/);
  assert.match(pdfText, /184[.,]00/);
  await pdfPage.close();
  console.log('PASS finalized annual PDF opened in browser; valid pages, both owners and 181/184 EUR verified');
  await page.goto(unitUrl);
  await persistedOwnership();
  assert.equal(ownershipCount(), 2);
  const ownershipPatches = mutations.filter(request => request.method() === 'PATCH'
    && new URL(request.url()).pathname.startsWith('/api/ownership/'));
  assert.equal(ownershipPatches.length, 1);
  assert.equal(new URL(ownershipPatches[0].url()).pathname, `/api/ownership/${original.id}`);
  assert.ok(!mutations.some(request => request.method() === 'DELETE'), 'Synthetic business records remain');
  authPhase = 'logging-out';
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
  await context.close();
  assert.deepEqual([...expectedAuth401].sort(), [...consoleAuth401].sort(),
    'Exact 1:1 anonymous/logout session-401 response/console pairing required');
  assertClean();
  console.log('PASS: UI sale 30.06.2026, actual WEG annual costs Seller 181.00 / Buyer 184.00, compute and final run persisted after reload.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
