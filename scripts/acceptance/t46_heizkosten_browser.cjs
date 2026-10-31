#!/usr/bin/env node
/* T46: real UI-only HeizkostenV acceptance against existing dev-local.sh.
 * Run: HV_PLAYWRIGHT_PACKAGE=/path/to/playwright node scripts/acceptance/t46_heizkosten_browser.cjs
 * Requires Chrome and pdftotext. No servers, SQL, fixtures, API mutations,
 * request client, mocks or report files. Synthetic UI records remain.
 * PDF is opened through the UI; only its exact URL may be read via browser CDP
 * if Chrome hides the original response body behind its built-in PDF viewer.
 */
'use strict';
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T46 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} Mietobjekt`;
const scopeName = `${prefix} Mietkreis`;
let receiptNumber = '';
let receiptAmount = '';
let receiptKind = '';

const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
let browser, context;
const hardTimeout = setTimeout(() => {
  console.error('FAIL T46: ten-minute hard timeout');
  process.exitCode = 1;
  void (async () => {
    try { if (context) await context.close(); }
    finally { if (browser) await browser.close(); }
  })().finally(() => process.exit(1));
  setTimeout(() => process.exit(1), 5000).unref();
}, 600000);

function receiptPdf() {
  const text = ['T46 SYNTHETIC INVOICE - NOT A REAL CLAIM', `Receipt ${receiptNumber}`,
    'Supplier: Synthetic Heating Supplier', 'Invoice date: 2026-12-15',
    'Service period: 2026-01-01 to 2026-12-31', `${receiptKind}: ${receiptAmount} EUR`,
    `VAT: 0.00 EUR; gross total: ${receiptAmount} EUR`];
  const escape = value => value.replace(/([\\()])/g, '\\$1');
  const stream = `BT /F1 12 Tf 50 780 Td 18 TL\n${text.map((line, i) => `${i ? 'T* ' : ''}(${escape(line)}) Tj`).join('\n')}\nET\n`;
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
  return Buffer.from(pdf);
}
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
  const fresh = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const request = value => { if (committed) fresh.add(value); };
  page.on('framenavigated', navigation);
  page.on('request', request);
  try {
    return await successfulJson(await responseFor(page,
      response => predicate(response) && fresh.has(response.request()), () => page.reload()));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', request);
  }
}
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
async function selectLabel(scope, label, option) {
  const control = field(scope, label);
  await control.getByRole('option', { name: option, exact: true }).waitFor({ state: 'attached' });
  await control.selectOption({ label: option });
}
async function selectCode(scope, label, code) {
  const control = field(scope, label);
  await eventually(async () => {
    const options = await control.getByRole('option').evaluateAll(items =>
      items.map(item => ({ value: item.value, label: item.label })));
    const matches = options.filter(item => item.value === code);
    assert.equal(matches.length, 1, `${label}: one live ${code} option required`);
    assert.ok(matches[0].label.trim(), `${label}: human label required`);
    await selectLabel(scope, label, matches[0].label);
  });
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, acceptDownloads: true });
  const errors = [], mutations = [], session401 = [], console401 = [], suggestions = [];
  const finalizedIds = new Set();
  const phaseAtRequest = new WeakMap();
  let authPhase = 'anonymous';
  const sessions = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) console401.push(`${authPhase} ${url}`);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('request', request => {
    phaseAtRequest.set(request, authPhase);
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) {
      if (![base, 'http://127.0.0.1:18000'].includes(url.origin)) errors.push(`Nonlocal API request: ${url}`);
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request);
      const match = url.pathname.match(/^\/api\/settlements\/([^/]+)\/(?:consumption-suggestions|heating-consumption)$/);
      if (match) {
        suggestions.push({ id: match[1], method: request.method() });
        if (finalizedIds.has(match[1])) errors.push(`Finalized run fetched live consumption: ${match[1]}`);
      }
    }
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    const phase = phaseAtRequest.get(response.request());
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(phase)) {
      session401.push(`${phase} ${response.url()}`);
    } else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  const clean = () => assert.deepEqual(errors, [], 'All unexpected HTTP/console/page errors are fatal');
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.setDefaultNavigationTimeout(30000);
  async function observe(method, pathname, action, status = 200) {
    const response = await responseFor(page, isApi(method, pathname), action);
    const json = await successfulJson(response, status);
    clean();
    return { response, json };
  }
  async function search(label) {
    await field(page, label).fill(prefix);
    await button(page.getByRole('search'), 'Suchen').click();
  }
  async function selectObserved(label, id, humanText) {
    await eventually(async () => {
      const options = await field(page, label).getByRole('option').evaluateAll(items =>
        items.map(item => ({ value: item.value, label: item.label })));
      const matches = options.filter(item => item.value === id);
      assert.equal(matches.length, 1, `${label}: one observed record required`);
      assert.ok(matches[0].label.includes(humanText), `${label}: expected readable name`);
      assert.ok(!matches[0].label.includes(id), `${label}: no UUID label fallback`);
      await selectLabel(page, label, matches[0].label);
    });
  }
  await page.goto(base + '/objekte');
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const { json: principal } = await observe('POST', '/api/auth/login', () => button(page, 'Anmelden').click());
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(propertyName);
  await selectCode(page, 'Verwaltungsart *', 'MIET');
  await field(page, 'Strasse').fill('Synthetischer Heizweg 46');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T46 synthetische Verbrauchsprüfung im isolierten lokalen Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'MIET');
  await search('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  const units = [], leases = [], people = [], meters = [], readings = [];
  for (const [index, area] of [40, 60].entries()) {
    await page.goto(propertyUrl);
    await button(page, '+ Neue Einheit').click();
    const number = `${prefix} Wohnung ${index + 1}`;
    const location = index === 0 ? 'Erdgeschoss links' : 'Obergeschoss rechts';
    await field(page, 'Nummer *').fill(number);
    await field(page, 'Lage').fill(location);
    await field(page, 'Flaeche (m2)').fill(String(area));
    const { json: unit } = await observe('POST', '/api/units', () => button(page, 'Speichern').click(), 201);
    assert.equal(unit.property_id, property.id);
    assert.equal(unit.location, location);
    assert.equal(Number(unit.area), area);
    await page.getByRole('link', { name: number, exact: true }).click();
    units.push({ ...unit, url: page.url() });
  }
  for (const [index, unit] of units.entries()) {
    const firstName = `${prefix} Mieter ${index + 1}`;
    const personName = `${firstName} Synthetisch`;
    people.push(personName);
    await page.goto(base + '/personen');
    await button(page, '+ Neue Person').click();
    await field(page, 'Vorname').fill(firstName);
    await field(page, 'Nachname *').fill('Synthetisch');
    const { json: person } = await observe('POST', '/api/persons', () => button(page, 'Speichern').click(), 201);
    await search('Personen suchen');
    await page.getByRole('link', { name: personName, exact: true }).click();
    const { json: tenant } = await observe('POST', '/api/tenant-profiles',
      () => button(page, 'Als Mieter kennzeichnen').click(), 201);
    assert.equal(tenant.person_id, person.id);
    await page.getByText('Mieter', { exact: true }).waitFor();
    await page.goto(unit.url);
    await button(page, '+ Mietvertrag').click();
    await selectLabel(page, 'Mieter *', personName);
    await field(page, 'Vertragsbeginn *').fill('2026-01-01');
    await field(page, 'Vertragsende').fill('2026-12-31');
    await field(page, 'Kaltmiete (EUR) *').fill('700.00');
    await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
    await field(page, 'Kaution (EUR)').fill('');
    const { json: lease } = await observe('POST', '/api/leases', () => button(page, 'Speichern').click(), 201);
    assert.equal(lease.unit_id, unit.id);
    assert.equal(lease.tenant_profile_id, tenant.id);
    assert.equal(lease.valid_from, '2026-01-01');
    assert.equal(lease.valid_to, '2026-12-31');
    assert.equal(lease.base_rent, '700.00');
    assert.equal(lease.advance_payment, '150.00');
    assert.equal(lease.deposit, null);
    leases.push(structuredClone(lease));
  }
  const panel = () => page.getByRole('region', { name: 'Zähler', exact: true });
  async function openHistory(meter) {
    const row = panel().getByRole('row').filter({ has: page.getByRole('cell', { name: meter.meter_number, exact: true }) });
    await row.waitFor();
    assert.equal(await row.count(), 1);
    return (await observe('GET', `/api/meters/${meter.id}/readings`,
      () => button(row, 'Verlauf anzeigen').click())).json;
  }
  async function addReading(meter, day, value) {
    await button(panel(), 'Stand erfassen').click();
    await field(panel(), 'Ablesedatum *').fill(day);
    await field(panel(), 'Zählerstand *').fill(value.replace('.', ','));
    await field(panel(), 'Zeitraum').fill('2026');
    const { response, json } = await observe('POST', `/api/meters/${meter.id}/readings`,
      () => button(panel(), 'Stand speichern').click(), 201);
    assert.deepEqual(response.request().postDataJSON(), { reading_date: day, value, period: '2026' });
    assert.equal(json.meter_id, meter.id);
    assert.equal(json.reading_date, day);
    assert.equal(json.value, value);
    await panel().getByRole('cell', { name: value.replace('.', ','), exact: true }).waitFor();
    return json;
  }
  for (const [index, unit] of units.entries()) {
    for (const [type, end] of [['HEIZUNG', index === 0 ? '200.0000' : '400.0000'],
      ['WARMWASSER', index === 0 ? '103.0000' : '101.0000']]) {
      await page.goto(unit.url);
      await button(panel(), 'Zähler anlegen').click();
      const number = `${prefix} ${type} ${index + 1}`;
      await field(panel(), 'Zählernummer *').fill(number);
      await field(panel(), 'Zählertyp *').fill(type);
      await field(panel(), 'Standort').fill(unit.location);
      const { json: meter } = await observe('POST', '/api/meters',
        () => button(panel(), 'Zähler speichern').click(), 201);
      assert.equal(meter.property_id, property.id);
      assert.equal(meter.unit_id, unit.id);
      assert.equal(meter.meter_type, type);
      assert.deepEqual(await openHistory(meter), []);
      meters.push(meter);
      readings.push(await addReading(meter, '2026-01-01', '100.0000'),
        await addReading(meter, '2026-12-31', end));
    }
  }
  assert.equal(meters.length, 4); assert.equal(readings.length, 8);
  await page.goto(base + '/berechtigungen/teamrechte');
  const propertyGroup = page.getByRole('group', { name: 'Objekt', exact: true });
  await field(propertyGroup, 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName);
  await field(page, 'Bezeichnung').fill(scopeName);
  await selectCode(page, 'Verwaltungsart', 'MIET');
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await observe('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click(), 201);
  assert.equal(scope.property_id, property.id);
  assert.equal(scope.scope_type, 'MIET');
  assert.equal(scope.whole_property, true);
  assert.equal(scope.valid_from, '2026-01-01');
  assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({
    has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }),
  });
  await grantForm.waitFor();
  await field(grantForm, 'Teammitglied suchen').fill('test@example.de');
  await eventually(async () => {
    const options = await field(grantForm, 'Teammitglied').getByRole('option').evaluateAll(items =>
      items.map(item => ({ value: item.value, label: item.label })));
    const matches = options.filter(item => item.value === principal.user_id);
    assert.equal(matches.length, 1);
    assert.ok(matches[0].label.includes('test@example.de'));
    await selectLabel(grantForm, 'Teammitglied', matches[0].label);
  });
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Freigeben', 'Exportieren']) await field(grantForm, label).check();
  for (const label of ['Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T46 ausdrückliche Rechte für beleggeprüfte Verbrauchsabrechnung');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'confirm', 'export', 'prepare', 'read']);
  async function selectScope() {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scope.id);
  }
  const costs = [], documents = [];
  for (const [code, kind, amount] of [['HEIZUNG_ZENTRAL', 'HEATING', '1000.00'],
    ['WARMWASSER_ZENTRAL', 'HOT_WATER', '400.00']]) {
    receiptKind = kind; receiptAmount = amount; receiptNumber = `${prefix} ${kind}`;
    const filename = `${prefix.replace(/ /g, '-')}-${kind}.pdf`;
    await page.goto(base + '/belege');
    await field(page, 'Belegdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: receiptPdf() });
    const { json: document } = await observe('POST', '/api/invoice-documents/upload', () => button(page, 'Hochladen').click(), 201);
    assert.equal(document.original_filename, filename);
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) })
      .getByRole('link', { name: 'Details', exact: true }).click();
    const { json: categories } = await observe('GET', '/api/cost-categories',
      () => button(page, 'Kostenposition erstellen').click());
    const matchingCategories = categories.filter(category => category.code === code);
    assert.equal(matchingCategories.length, 1, `Canonical ${code} category required`);
    const category = matchingCategories[0];
    await selectLabel(page, 'Objekt *', propertyName);
    await selectLabel(page, 'Kategorie *', `${category.code} ${category.label}`);
    await field(page, 'Rechnungsdatum *').fill('2026-12-15');
    await field(page, 'Leistungszeitraum von *').fill('2026-01-01');
    await field(page, 'Leistungszeitraum bis *').fill('2026-12-31');
    await field(page, 'Bruttobetrag *').fill(amount);
    await field(page, 'USt-Betrag').fill('0.00');
    await field(page, 'USt-Satz (%)').fill('0');
    await field(page, 'Belegnummer').fill(receiptNumber);
    await selectLabel(page, 'Umlagefaehigkeit *', 'Voll umlagefaehig');
    assert.equal(await field(page, 'Umlagefaehigkeit *').inputValue(), 'UMLAGEFAEHIG');
    await selectScope();
    const { response: costResponse, json: cost } = await observe('POST', '/api/cost-entries',
      () => button(page, 'Entwurf erstellen').click(), 201);
    const costInput = costResponse.request().postDataJSON();
    assert.equal(costInput.source_document_id, document.id);
    assert.equal(costInput.accounting_scope_id, scope.id);
    assert.equal(costInput.tenant_apportionable, 'UMLAGEFAEHIG');
    assert.equal(cost.property_id, property.id);
    assert.equal(cost.gross_amount, amount);
    await page.waitForURL(base + `/kosten/${cost.id}`);
    await button(page, 'Bearbeiten').click();
    await selectCode(page, 'Verteilerschl. Mieter', 'EXTERNAL');
    const { json: edited } = await observe('PATCH', `/api/cost-entries/${cost.id}`, () => button(page, 'Speichern').click());
    assert.equal(edited.tenant_distribution_key, 'EXTERNE_ABRECHNUNG');
    assert.equal(edited.tenant_apportionable, 'UMLAGEFAEHIG');
    const { json: confirmed } = await observe('POST', `/api/cost-entries/${cost.id}/confirm`, () => button(page, 'Bestaetigen').click());
    assert.equal(confirmed.status, 'confirmed');
    assert.equal(confirmed.gross_amount, amount);
    assert.equal(confirmed.tenant_distribution_key, 'EXTERNE_ABRECHNUNG');
    assert.equal(confirmed.service_period_from, '2026-01-01');
    assert.equal(confirmed.service_period_to, '2026-12-31');
    assert.deepEqual(await reloadJson(page, isApi('GET', `/api/cost-entries/${cost.id}`)), confirmed);
    const { json: released } = await observe('GET', `/api/invoice-documents/${document.id}`,
      () => page.goto(base + `/belege/${document.id}`));
    assert.equal(released.cost_entry_id, cost.id);
    assert.ok(released.released_at, 'Confirmed real cost releases the uploaded receipt');
    costs.push({ ...confirmed, kind, categoryLabel: category.label });
    documents.push({ ...released, receiptNumber });
  }
  const expectedHeating = costs.map(cost => ({ cost_entry_id: cost.id, kind: cost.kind,
    consumption_fraction: cost.kind === 'HEATING' ? '0.70' : '0.50', base_key: 'AREA',
    consumption_recorded: true, mandatory_seventy_percent: false,
    meter_type: cost.kind === 'HEATING' ? 'HEIZUNG' : 'WARMWASSER', slices: [] }));
  const expectedBody = { heating_inputs: expectedHeating, external_amounts: [], manual_amounts: [], unit_metrics: [] };
  await page.goto(base + '/abrechnungen');
  await button(page, '+ Neue Abrechnung').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectObserved('Mietvertrag *', leases[0].id, people[0]);
  await field(page, 'Abrechnungszeitraum von *').fill('2026-01-01');
  await field(page, 'Abrechnungszeitraum bis *').fill('2026-12-31');
  await field(page, 'Vorauszahlungssumme').fill('0.00');
  await selectScope();
  const { json: run } = await observe('POST', '/api/settlements', () => button(page, 'Anlegen').click(), 201);
  assert.equal(run.lease_agreement_id, leases[0].id);
  assert.equal(run.status, 'draft');
  await page.waitForURL(base + `/abrechnungen/${run.id}`);
  const runPath = `/api/settlements/${run.id}`;
  const heating = page.getByRole('region', { name: 'Interne Heizkostenverordnung', exact: true });
  function group(cost) {
    // Each category occurs exactly once in this synthetic property.
    return heating.getByRole('group', { name: cost.categoryLabel, exact: true });
  }
  function assertCandidates(rows, cost) {
    assert.equal(rows.length, 2, 'Both full-year unit slices are required');
    assert.deepEqual(rows.map(row => row.unit_id).sort(), units.map(unit => unit.id).sort());
    for (const [index, unit] of units.entries()) {
      const row = rows.find(item => item.unit_id === unit.id);
      assert.equal(row.unit_label, unit.number);
      assert.equal(row.period_from, '2026-01-01'); assert.equal(row.period_to, '2026-12-31');
      assert.equal(Number(row.base_quantity), index === 0 ? 40 : 60);
      assert.equal(Number(row.consumption), cost.kind === 'HEATING' ? [100, 300][index] : [3, 1][index]);
      const meter = meters.find(item => item.unit_id === unit.id && item.meter_type === (cost.kind === 'HEATING' ? 'HEIZUNG' : 'WARMWASSER'));
      assert.equal(row.meter_id, meter.id); assert.equal(row.reason, null);
      const ownReadings = readings.filter(reading => reading.meter_id === meter.id);
      assert.equal(ownReadings.length, 2);
      for (const [key, date] of [['start_reading', '2026-01-01'], ['end_reading', '2026-12-31']]) {
        const reading = ownReadings.find(item => item.reading_date === date);
        assert.deepEqual(row[key], { id: reading.id, reading_date: date, value: reading.value });
      }
    }
  }
  for (const cost of costs) {
    const controls = group(cost); await controls.waitFor(); assert.equal(await controls.count(), 1);
    await controls.getByRole('checkbox', { name: /^Intern nach HeizkostenV abrechnen:/ }).check();
    await field(controls, 'Verbrauchsanteil (0,50–0,70)').fill(cost.kind === 'HEATING' ? '0,70' : '0,50');
    await controls.getByRole('combobox', { name: 'Grundkostenschlüssel', exact: true }).selectOption('AREA');
    await field(controls, 'Verbrauch erfasst').check();
    const type = cost.kind === 'HEATING' ? 'HEIZUNG' : 'WARMWASSER';
    const candidateResponse = await responseFor(page, response => {
      const url = new URL(response.url());
      return isApi('GET', `${runPath}/heating-consumption`)(response)
        && url.searchParams.get('cost_entry_id') === cost.id
        && url.searchParams.get('meter_type') === type && url.searchParams.get('base_key') === 'AREA';
    }, () => field(controls, 'Zählertyp').fill(type));
    const rows = await successfulJson(candidateResponse);
    assertCandidates(rows, cost);
    for (const [index, unit] of units.entries()) {
      const label = `${unit.number} 2026-01-01–2026-12-31`;
      await eventually(async () => {
        assert.equal(Number(await field(controls, `Grundmenge ${label}`).inputValue()), [40, 60][index]);
        assert.equal(Number(await field(controls, `Heizverbrauch ${label}`).inputValue()), cost.kind === 'HEATING' ? [100, 300][index] : [3, 1][index]);
      });
    }
  }
  function assertAnnual(value) {
    assert.equal(value.lease_agreement_id, leases[0].id);
    assert.equal(value.period_start, '2026-01-01'); assert.equal(value.period_end, '2026-12-31');
    assert.equal(value.advance_payments_total, '0.00');
    assert.equal(value.total_cost, '525.00'); assert.equal(value.balance, '525.00');
    const lines = value.lines.filter(line => line.cost_entry_id);
    assert.equal(lines.length, 2);
    for (const cost of costs) {
      const matches = lines.filter(line => line.cost_entry_id === cost.id);
      assert.equal(matches.length, 1); assert.equal(matches[0].amount, cost.kind === 'HEATING' ? '295.00' : '230.00');
      assert.equal(matches[0].total_amount, cost.gross_amount);
    }
  }
  async function coverage(stage) {
    const section = page.getByRole('region', { name: 'Datengrundlage bestätigen', exact: true });
    await field(section, 'Grundlage der Prüfung').fill(`T46 ${stage}: vollständiges synthetisches Jahr 2026, zwei freigegebene Belege Heizung 1000 EUR und Warmwasser 400 EUR, Flächen 40/60, getrennte Zählerverbräuche 100/300 und 3/1. Keine weiteren Kosten oder Zahlungen; tatsächlich gezahlte Vorauszahlungen ausdrücklich 0 EUR. Interne Verteilung 70 % / 50 %, kein externer Messdienst.`);
    let sources = (await observe('GET', '/api/financial-input-coverage/source-options',
      () => button(section, 'Freigegebene Belege auswählen').click())).json;
    const found = [...sources.items];
    while (sources.next_cursor) {
      sources = (await observe('GET', '/api/financial-input-coverage/source-options',
        () => button(section, 'Weitere Belege laden').click())).json;
      found.push(...sources.items);
    }
    for (const document of documents) {
      assert.equal(found.filter(source => source.id === document.id).length, 1);
      await field(section, `${document.receiptNumber} · Version ${document.version}`).check();
    }
    await field(section, 'Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge.').check();
    const { response } = await observe('POST', '/api/financial-input-coverage',
      () => button(section, 'Datengrundlage speichern').click(), 201);
    const body = response.request().postDataJSON();
    assert.equal(body.subject_kind, 'settlement'); assert.equal(body.subject_id, run.id);
    assert.equal(body.confirmed_complete, true); assert.equal(body.values.advance_payments_total, '0.00');
    assert.deepEqual(body.source_document_ids.sort(), documents.map(document => document.id).sort());
    await section.getByRole('status').filter({ hasText: 'Prüfung gespeichert.' }).waitFor();
  }
  await coverage('vor expliziter Vorschau');
  const beforePreview = mutations.filter(request => request.url().endsWith(`${runPath}/preview`)).length;
  assert.equal(beforePreview, 0, 'Changing heating settings must not implicitly preview');
  const preview = await observe('POST', `${runPath}/preview`, () => button(page, 'Vorschau').click());
  assert.deepEqual(preview.response.request().postDataJSON(), expectedBody);
  assertAnnual(preview.json);
  await page.getByRole('heading', { name: 'Vorschau (nicht gespeichert)', exact: true }).waitFor();
  await button(page, 'Vorschau schliessen').click();
  const inspection = await context.newPage(); inspection.setDefaultTimeout(20000);
  try {
    const unchanged = await successfulJson(await responseFor(inspection, isApi('GET', runPath),
      () => inspection.goto(base + `/abrechnungen/${run.id}`)));
    assert.equal(unchanged.status, 'draft');
    assert.equal(unchanged.total_cost, run.total_cost);
    assert.deepEqual(unchanged.lines, run.lines, 'Explicit preview must not persist result lines');
    assert.deepEqual(unchanged.extra_inputs, run.extra_inputs, 'Preview must not save configuration');
  } finally { await inspection.close(); }
  const computed = await observe('POST', `${runPath}/compute`, () => button(page, 'Berechnen').click());
  assert.deepEqual(computed.response.request().postDataJSON(), expectedBody);
  assertAnnual(computed.json); assert.deepEqual(computed.json.extra_inputs, expectedBody);
  for (const cost of costs) {
    const row = page.getByRole('row').filter({ has: page.locator(`a[href="/kosten/${cost.id}"]`) });
    await row.getByRole('cell', { name: `${cost.kind === 'HEATING' ? '295.00' : '230.00'} EUR`, exact: true }).waitFor();
    await row.getByRole('cell', { name: `${cost.gross_amount} EUR`, exact: true }).waitFor();
    await row.getByRole('cell', { name: 'HeizkostenV intern', exact: true }).waitFor();
  }
  const persisted = await reloadJson(page, isApi('GET', runPath));
  assertAnnual(persisted); assert.deepEqual(persisted.extra_inputs, expectedBody);
  assert.deepEqual(persisted.lines, computed.json.lines);
  // Rehydration uses the saved per-cost settings, not generic external amounts.
  for (const cost of costs) {
    const controls = group(cost);
    await controls.getByRole('checkbox', { name: /^Intern nach HeizkostenV abrechnen:/ }).waitFor();
    assert.equal(await controls.getByRole('checkbox', { name: /^Intern nach HeizkostenV abrechnen:/ }).isChecked(), true);
    assert.equal(Number(await field(controls, 'Verbrauchsanteil (0,50–0,70)').inputValue()), cost.kind === 'HEATING' ? 0.7 : 0.5);
    assert.equal(await controls.getByRole('combobox', { name: 'Grundkostenschlüssel', exact: true }).inputValue(), 'AREA');
    assert.equal(await field(controls, 'Zählertyp').inputValue(), cost.kind === 'HEATING' ? 'HEIZUNG' : 'WARMWASSER');
    assert.equal(await field(controls, 'Verbrauch erfasst').isChecked(), true);
    for (const [index, unit] of units.entries()) {
      await eventually(async () => assert.equal(Number(await field(controls,
        `Heizverbrauch ${unit.number} 2026-01-01–2026-12-31`).inputValue()), cost.kind === 'HEATING' ? [100, 300][index] : [3, 1][index]));
    }
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await coverage('nach Reload, Telefonansicht vor Finalisierung');
  const finalized = await observe('POST', `${runPath}/finalize`, async () => {
    const dialogPromise = page.waitForEvent('dialog');
    const clickPromise = button(page, 'Finalisieren').click();
    const dialog = await dialogPromise; assert.equal(dialog.type(), 'confirm');
    assert.match(dialog.message(), /finalisieren/); await dialog.accept(); await clickPromise;
  });
  assert.deepEqual(finalized.response.request().postDataJSON(), expectedBody);
  assertAnnual(finalized.json); assert.equal(finalized.json.status, 'finalized');
  assert.match(finalized.json.snapshot_sha256, /^[a-f0-9]{64}$/);
  finalizedIds.add(run.id);
  const frozen = await reloadJson(page, isApi('GET', runPath));
  assertAnnual(frozen); assert.equal(frozen.status, 'finalized');
  assert.deepEqual(frozen.extra_inputs, expectedBody);
  assert.equal(frozen.snapshot_sha256, finalized.json.snapshot_sha256);
  await button(page, 'PDF herunterladen').waitFor();
  assert.equal(await heating.count(), 0, 'Finalized heating settings are read-only');
  const [popup, pdfResponse] = await Promise.all([
    page.waitForEvent('popup'),
    context.waitForEvent('response', { predicate: isApi('GET', `${runPath}/pdf`) }),
    button(page, 'PDF herunterladen').click(),
  ]);
  assert.equal(pdfResponse.status(), 200);
  assert.match(pdfResponse.headers()['content-type'], /application\/pdf/);
  await popup.waitForLoadState('load');
  assert.equal(new URL(popup.url()).pathname, `${runPath}/pdf`);
  let bytes;
  try {
    bytes = await pdfResponse.body();
    if (!bytes.subarray(0, 5).equals(Buffer.from('%PDF-'))) bytes = undefined;
  } catch (error) {
    // Chromium's PDF viewer can consume the network body before Playwright.
    console.log(`Inline PDF body unavailable; reading the same UI-opened URL through browser CDP: ${error.message}`);
  }
  if (!bytes) {
    const cdp = await context.newCDPSession(popup);
    let stream;
    try {
      const { frameTree } = await cdp.send('Page.getFrameTree');
      const { resource } = await cdp.send('Network.loadNetworkResource', { frameId: frameTree.frame.id, url: pdfResponse.url(),
        options: { disableCache: false, includeCredentials: true } });
      stream = resource.stream;
      assert.equal(resource.success, true, JSON.stringify(resource));
      assert.equal(resource.httpStatusCode, 200); assert.ok(stream, 'Browser PDF resource stream required');
      const chunks = []; let length = 0;
      for (;;) {
        const part = await cdp.send('IO.read', { handle: stream, size: 65536 });
        const chunk = Buffer.from(part.data, part.base64Encoded ? 'base64' : 'utf8');
        chunks.push(chunk); length += chunk.length;
        assert.ok(length < 25 * 1024 * 1024, 'Synthetic PDF must stay bounded');
        if (part.eof) break;
      }
      bytes = Buffer.concat(chunks);
    } finally {
      try { if (stream) await cdp.send('IO.close', { handle: stream }); }
      finally { await cdp.detach(); }
    }
  }
  assert.ok(bytes.subarray(0, 5).equals(Buffer.from('%PDF-')));
  const text = spawnSync('pdftotext', ['-layout', '-', '-'], { input: bytes,
    encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
  assert.ifError(text.error); assert.equal(text.status, 0, text.stderr);
  const normalized = text.stdout.replace(/\s+/g, ' ');
  for (const expected of ['Grundanteil', 'Verbrauchsanteil', '120,00', '175,00', '295,00', '80,00', '150,00', '230,00', '525,00']) {
    assert.ok(normalized.includes(expected), `Actual UI-opened PDF missing ${expected}: ${text.stdout}`);
  }
  await popup.close(); clean();
  console.log('T46 UI PASS: two 2026 leases, areas40/60; confirmed heating1000 and hot-water400; boundary meter usage100/300 and3/1; internal70%/50% area settings, explicit preview, compute/reload restores exact typed config, finalized525EUR; phone viewport; UI popup PDF200 and actual Grundanteil/Verbrauchsanteil120,00/175,00/295,00/230,00/525,00. No external delivery.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  clearTimeout(hardTimeout);
  try { if (context) await context.close(); }
  finally { if (browser) await browser.close(); }
});
