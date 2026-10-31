#!/usr/bin/env node
/* T31: UI-only meter-derived annual tenant water costs: 100 EUR * 30/100
 * and 100 EUR * 70/100. Root starts isolated UI13000/API18000 after green.
 * No request client, API/SQL seeds, route mocks, downloads or fixture files.
 * Synthetic records remain. PASS requires actual browser execution by Root.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T31 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} Mietobjekt`;
const scopeName = `${prefix} Mietkreis`;
const receiptNumber = `${prefix} Kaltwasser`;
const filename = `${prefix.replace(/ /g, '-')}.pdf`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
let browser, context;
const hardTimeout = setTimeout(() => {
  console.error('FAIL T31: ten-minute hard timeout');
  process.exitCode = 1;
  void (async () => {
    try { if (context) await context.close(); }
    finally { if (browser) await browser.close(); }
  })().finally(() => process.exit(1));
  setTimeout(() => process.exit(1), 5000).unref();
}, 600000);

function receiptPdf() {
  const text = ['T31 SYNTHETIC INVOICE - NOT A REAL CLAIM', `Receipt ${receiptNumber}`,
    'Supplier: Synthetic Cold Water Fixture', 'Invoice date: 2026-12-15',
    'Service period: 2026-01-01 to 2026-12-31', 'Cold water: 100.00 EUR',
    'VAT: 0.00 EUR; gross total: 100.00 EUR'];
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
  context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, acceptDownloads: false });
  const errors = [], mutations = [], session401 = [], console401 = [], suggestions = [];
  const finalizedIds = new Set();
  const phaseAtRequest = new WeakMap();
  let authPhase = 'anonymous';
  const sessions = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('download', () => errors.push('Unexpected download'));
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
      const match = url.pathname.match(/^\/api\/settlements\/([^/]+)\/consumption-suggestions$/);
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
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await observe('POST', '/api/auth/login', () => button(page, 'Anmelden').click());
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(propertyName);
  await selectCode(page, 'Verwaltungsart *', 'MIET');
  await field(page, 'Strasse').fill('Synthetischer Wasserweg 31');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T31 synthetische Verbrauchsprüfung im isolierten lokalen Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'MIET');
  await search('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  const units = [], leases = [], people = [], meters = [], readings = [];
  for (const [index, area] of [50, 70].entries()) {
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
    await field(page, 'Kaltmiete (EUR) *').fill('700.00');
    await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
    await field(page, 'Kaution (EUR)').fill('');
    const { json: lease } = await observe('POST', '/api/leases', () => button(page, 'Speichern').click(), 201);
    assert.equal(lease.unit_id, unit.id);
    assert.equal(lease.tenant_profile_id, tenant.id);
    assert.equal(lease.valid_from, '2026-01-01');
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
  async function preserved(index) {
    const unit = units[index], lease = leases[index], meter = meters[index];
    await page.goto(unit.url);
    const current = await reloadJson(page, response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id);
    assert.deepEqual(current.filter(item => item.id === lease.id), [lease], 'Original lease unchanged');
    assert.deepEqual(await reloadJson(page, isApi('GET', `/api/leases/${lease.id}/rent-changes`)), [], 'No rent-history append');
    const leaseRow = page.getByRole('row').filter({ has: page.getByRole('cell', { name: people[index], exact: true }) });
    await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
    await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();
    const currentMeters = await reloadJson(page, isApi('GET', '/api/meters'));
    assert.deepEqual(currentMeters, [meter], 'Exactly one unchanged unit meter');
    assert.deepEqual(await openHistory(meter), readings[index], 'Four-decimal original history unchanged after reload');
    await panel().getByRole('status', { name: 'Verbrauch', exact: true })
      .filter({ hasText: `Verbrauch: ${index === 0 ? '30,0000' : '70,0000'}` }).waitFor();
    clean();
  }
  for (const [index, unit] of units.entries()) {
    await page.goto(unit.url);
    await button(panel(), 'Zähler anlegen').click();
    const number = `${prefix} KALTWASSER ${index + 1}`;
    await field(panel(), 'Zählernummer *').fill(number);
    await field(panel(), 'Zählertyp *').fill('KALTWASSER');
    await field(panel(), 'Standort').fill(unit.location);
    const { response, json: meter } = await observe('POST', '/api/meters', () => button(panel(), 'Zähler speichern').click(), 201);
    const body = response.request().postDataJSON();
    assert.equal(body.property_id, property.id);
    assert.equal(body.unit_id, unit.id);
    assert.equal(body.meter_type, 'KALTWASSER');
    assert.equal(meter.unit_id, unit.id);
    assert.equal(meter.meter_number, number);
    assert.equal(meter.meter_type, 'KALTWASSER');
    meters.push(meter);
    assert.deepEqual(await openHistory(meter), []);
    readings.push([await addReading(meter, '2026-01-01', '100.0000'),
      await addReading(meter, '2026-12-31', index === 0 ? '130.0000' : '170.0000')]);
    await preserved(index);
  }
  await page.goto(propertyUrl);
  const propertyMeters = await reloadJson(page, isApi('GET', '/api/meters'));
  assert.deepEqual(propertyMeters.map(item => item.id).sort(), meters.map(item => item.id).sort());
  const originalCounts = { leases: leases.length, meters: meters.length, readings: readings.flat().length };
  assert.deepEqual(originalCounts, { leases: 2, meters: 2, readings: 4 });

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
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Freigeben']) await field(grantForm, label).check();
  for (const label of ['Exportieren', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T31 ausdrückliche Rechte für beleggeprüfte Verbrauchsabrechnung');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'confirm', 'prepare', 'read']);
  async function selectScope() {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scope.id);
  }
  await page.goto(base + '/belege');
  await field(page, 'Belegdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: receiptPdf() });
  const { json: document } = await observe('POST', '/api/invoice-documents/upload', () => button(page, 'Hochladen').click(), 201);
  assert.equal(document.original_filename, filename);
  await page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) })
    .getByRole('link', { name: 'Details', exact: true }).click();
  await button(page, 'Kostenposition erstellen').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await eventually(async () => {
    const labels = (await field(page, 'Kategorie *').getByRole('option').allTextContents()).map(value => value.trim());
    const matches = labels.filter(label => /\b(Kaltwasser|Wasserversorgung|Wasser)\b/i.test(label)
      && !/Abwasser|Warmwasser|Schmutzwasser|Niederschlagswasser/i.test(label));
    assert.equal(matches.length, 1, `Prerequisite: exactly one canonical cold-water category; observed ${JSON.stringify(matches)}`);
    await selectLabel(page, 'Kategorie *', matches[0]);
  });
  await field(page, 'Rechnungsdatum *').fill('2026-12-15');
  await field(page, 'Leistungszeitraum von *').fill('2026-01-01');
  await field(page, 'Leistungszeitraum bis *').fill('2026-12-31');
  await field(page, 'Bruttobetrag *').fill('100.00');
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
  assert.equal(cost.gross_amount, '100.00');
  await page.waitForURL(base + `/kosten/${cost.id}`);
  await button(page, 'Bearbeiten').click();
  await selectLabel(page, 'Verteilerschl. Mieter', 'Verbrauch');
  const { json: edited } = await observe('PATCH', `/api/cost-entries/${cost.id}`, () => button(page, 'Speichern').click());
  assert.equal(edited.tenant_distribution_key, 'VERBRAUCH');
  assert.equal(edited.tenant_apportionable, 'UMLAGEFAEHIG');
  const { json: confirmed } = await observe('POST', `/api/cost-entries/${cost.id}/confirm`, () => button(page, 'Bestaetigen').click());
  assert.equal(confirmed.status, 'confirmed');
  assert.equal(confirmed.gross_amount, '100.00');
  assert.equal(confirmed.tenant_distribution_key, 'VERBRAUCH');
  assert.equal(confirmed.service_period_from, '2026-01-01');
  assert.equal(confirmed.service_period_to, '2026-12-31');
  assert.deepEqual(await reloadJson(page, isApi('GET', `/api/cost-entries/${cost.id}`)), confirmed);
  const { json: released } = await observe('GET', `/api/invoice-documents/${document.id}`,
    () => page.goto(base + `/belege/${document.id}`));
  assert.equal(released.cost_entry_id, cost.id);
  assert.ok(released.released_at, 'Confirmed real cost releases the uploaded receipt');

  function assertSuggestions(values) {
    assert.equal(values.length, 2, 'All scope units, not just the selected tenant');
    assert.deepEqual(values.map(value => value.unit_id).sort(), units.map(unit => unit.id).sort());
    for (const [index, unit] of units.entries()) {
      const value = values.find(item => item.unit_id === unit.id);
      assert.equal(value.unit_label, unit.number);
      assert.equal(value.consumption, index === 0 ? '30.0000' : '70.0000');
      assert.equal(value.meter_id, meters[index].id);
      assert.equal(value.reason, null);
      for (const [key, row] of [['start_reading', readings[index][0]], ['end_reading', readings[index][1]]]) {
        assert.deepEqual(value[key], { id: row.id, reading_date: row.reading_date, value: row.value });
      }
    }
  }
  async function structuredInputs() {
    await button(page, 'Strukturierte Eingaben anzeigen').click();
    assert.equal(await page.getByRole('textbox', { name: /^Verbrauch \(/ }).count(), 2);
    for (const [index, unit] of units.entries()) {
      const control = field(page, `Verbrauch (${unit.number} (${unit.location}))`);
      assert.equal(await control.inputValue(), index === 0 ? '30.0000' : '70.0000');
      const label = page.getByText(`Zählerbasis: 100.0000 (2026-01-01) → ${index === 0 ? '130.0000' : '170.0000'} (2026-12-31), jeweils ±31 Tage. Wird bei Berechnung frisch geprüft.`, { exact: true });
      await label.waitFor();
    }
  }
  async function assertRenderedLine(value) {
    const line = value.lines.find(item => item.cost_entry_id === cost.id);
    const row = page.getByRole('row').filter({ has: page.getByRole('link', { name: 'Kostenposition und Beleg ansehen', exact: true }) });
    await row.waitFor();
    assert.equal(await row.count(), 1);
    for (const text of ['100.00 EUR', 'Verbrauch', `${line.distribution_base_share} / ${line.distribution_base_total}`, '365 / 365 Tage', `${line.amount} EUR`]) {
      await row.getByRole('cell', { name: text, exact: true }).waitFor();
    }
  }
  function assertAnnual(value, index) {
    const share = index === 0 ? '30' : '70';
    assert.equal(value.lease_agreement_id, leases[index].id);
    assert.equal(value.period_start, '2026-01-01');
    assert.equal(value.period_end, '2026-12-31');
    assert.equal(value.total_cost, `${share}.00`);
    assert.equal(value.advance_payments_total, '0.00');
    assert.equal(value.balance, `${share}.00`);
    assert.ok(value.lines.length > 0);
    const costs = value.lines.filter(line => line.cost_entry_id === cost.id);
    assert.equal(costs.length, 1, 'Exactly the genuine water cost line');
    const line = costs[0];
    assert.equal(line.total_amount, '100.00');
    assert.equal(line.distribution_key, 'VERBRAUCH');
    assert.match(line.distribution_base_total, /^100(?:\.0+)?$/);
    assert.match(line.distribution_base_share, new RegExp(`^${share}(?:\\.0+)?$`));
    assert.equal(line.time_share_days, 365);
    assert.equal(line.time_share_total_days, 365);
    assert.equal(line.amount, `${share}.00`);
    assert.equal(value.lines.filter(item => item.cost_entry_id && item.cost_entry_id !== cost.id).length, 0);
  }
  async function coverage(run, stage) {
    const section = page.getByRole('region', { name: 'Datengrundlage bestätigen', exact: true });
    await field(section, 'Grundlage der Prüfung').fill(`T31 ${stage}: vollständig geprüftes Jahr 2026; ausschließlich freigegebener Kaltwasserbeleg 100 EUR für beide ganzjährig vermieteten Einheiten; Zähler 100→130 und 100→170, Verbrauch 30/70; tatsächlich gezahlte Vorauszahlungen ausdrücklich 0,00 EUR, keine Zahlungen, keine weiteren Kosten oder manuellen/externalen Beträge.`);
    const { json: sources } = await observe('GET', '/api/financial-input-coverage/source-options',
      () => button(section, 'Freigegebene Belege auswählen').click());
    assert.equal(sources.next_cursor, null);
    assert.equal(sources.items.filter(item => item.id === document.id).length, 1);
    await field(section, `${receiptNumber} · Version ${released.version}`).check();
    await field(section, 'Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge.').check();
    const { response } = await observe('POST', '/api/financial-input-coverage',
      () => button(section, 'Datengrundlage speichern').click(), 201);
    const payload = response.request().postDataJSON();
    assert.equal(payload.subject_kind, 'settlement');
    assert.equal(payload.subject_id, run.id);
    assert.equal(payload.confirmed_complete, true);
    assert.deepEqual(payload.source_document_ids, [document.id]);
    assert.equal(payload.values.advance_payments_total, '0.00');
    await section.getByRole('status').filter({ hasText: 'Prüfung gespeichert.' }).waitFor();
  }
  const runs = [];
  for (const [index, lease] of leases.entries()) {
    await page.goto(base + '/abrechnungen');
    await button(page, '+ Neue Abrechnung').click();
    await selectLabel(page, 'Objekt *', propertyName);
    await selectObserved('Mietvertrag *', lease.id, people[index]);
    await field(page, 'Abrechnungszeitraum von *').fill('2026-01-01');
    await field(page, 'Abrechnungszeitraum bis *').fill('2026-12-31');
    await field(page, 'Vorauszahlungssumme').fill('0.00');
    await selectScope();
    const { response, json: run } = await observe('POST', '/api/settlements', () => button(page, 'Anlegen').click(), 201);
    assert.equal(response.request().postDataJSON().accounting_scope_id, scope.id);
    assert.equal(response.request().postDataJSON().advance_payments_total, '0.00');
    assert.equal(run.lease_agreement_id, lease.id);
    assert.equal(run.advance_payments_total, '0.00');
    assert.equal(run.status, 'draft');
    await page.waitForURL(base + `/abrechnungen/${run.id}`);
    const runPath = `/api/settlements/${run.id}`;
    assertSuggestions(await reloadJson(page, isApi('GET', `${runPath}/consumption-suggestions`)));
    await structuredInputs();
    await coverage(run, 'vor Berechnung');
    const computed = await observe('POST', `${runPath}/compute`, () => button(page, 'Berechnen').click());
    assert.deepEqual(computed.response.request().postDataJSON(), { external_amounts: [], manual_amounts: [], unit_metrics: [] },
      'Automatic values must NEVER be echoed as manual metrics');
    assertAnnual(computed.json, index);
    await assertRenderedLine(computed.json);
    const reloaded = await reloadJson(page, isApi('GET', runPath));
    assertAnnual(reloaded, index);
    assert.deepEqual(reloaded.lines, computed.json.lines);
    // Reload again to await the actual fresh suggestions before checking both inputs.
    assertSuggestions(await reloadJson(page, isApi('GET', `${runPath}/consumption-suggestions`)));
    await structuredInputs();
    await coverage(run, 'erneute Prüfung nach Reload vor Finalisierung');
    const finalized = await observe('POST', `${runPath}/finalize`, async () => {
      const dialogPromise = page.waitForEvent('dialog');
      const clickPromise = button(page, 'Finalisieren').click();
      const dialog = await dialogPromise;
      assert.equal(dialog.type(), 'confirm');
      assert.equal(dialog.message(), 'Abrechnung finalisieren? Nach der Finalisierung ist die Abrechnung unveraenderbar (GoBD). Aenderungen sind nur als Korrekturabrechnung moeglich.');
      await dialog.accept();
      await clickPromise;
    });
    assert.deepEqual(finalized.response.request().postDataJSON(), { external_amounts: [], manual_amounts: [], unit_metrics: [] });
    assertAnnual(finalized.json, index);
    assert.equal(finalized.json.status, 'finalized');
    assert.match(finalized.json.snapshot_sha256, /^[a-f0-9]{64}$/);
    assert.ok(finalized.json.finalized_at);
    finalizedIds.add(run.id);
    const immutable = structuredClone(finalized.json);
    assert.deepEqual(await reloadJson(page, isApi('GET', runPath)), immutable, 'Entire finalized JSON/hash/lines immutable');
    await assertRenderedLine(immutable);
    await button(page, 'Strukturierte Eingaben anzeigen').waitFor({ state: 'hidden' });
    assert.deepEqual(await reloadJson(page, isApi('GET', runPath)), immutable);
    assert.ok(suggestions.some(item => item.id === run.id && item.method === 'GET'));
    runs.push(immutable);
    clean();
  }
  assert.equal(BigInt(runs[0].total_cost.replace('.', '')) + BigInt(runs[1].total_cost.replace('.', '')), 10000n);
  for (let index = 0; index < units.length; index++) await preserved(index);
  await page.goto(propertyUrl);
  const finalMeters = await reloadJson(page, isApi('GET', '/api/meters'));
  assert.deepEqual(finalMeters.map(item => item.id).sort(), meters.map(item => item.id).sort(), 'No extra counters');
  const allowed = new Map([
    ['POST /api/auth/login', 1], ['POST /api/properties', 1], ['POST /api/units', 2],
    ['POST /api/persons', 2], ['POST /api/tenant-profiles', 2], ['POST /api/leases', 2],
    ['POST /api/meters', 2], ...meters.map(meter => [`POST /api/meters/${meter.id}/readings`, 2]),
    ['POST /api/accounting-scopes', 1], [`POST /api/accounting-scopes/${scope.id}/grants`, 1],
    ['POST /api/invoice-documents/upload', 1], ['POST /api/cost-entries', 1],
    [`PATCH /api/cost-entries/${cost.id}`, 1], [`POST /api/cost-entries/${cost.id}/confirm`, 1],
    ['POST /api/settlements', 2], ['POST /api/financial-input-coverage', 4],
    ...runs.flatMap(run => [[`POST /api/settlements/${run.id}/compute`, 1], [`POST /api/settlements/${run.id}/finalize`, 1]]),
    ['POST /api/auth/logout', 1],
  ]);
  authPhase = 'logging-out';
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
  await context.close();
  const actual = new Map();
  for (const request of mutations) {
    const key = `${request.method()} ${new URL(request.url()).pathname}`;
    actual.set(key, (actual.get(key) || 0) + 1);
  }
  assert.deepEqual([...actual].sort(), [...allowed].sort(), 'Exact enumerated UI writes only; no rent/payment/bank/special-levy mutations');
  assert.ok(session401.some(value => value.startsWith('anonymous ')), 'Initial anonymous session guard exercised');
  assert.deepEqual([...session401].sort(), [...console401].sort(), 'Exact phase-bound session GET401/console pairing');
  clean();
  await browser.close();
  browser = null;
  console.log('PASS T31: UI-only 2 leases/2 meters/4 readings; actual suggestions 30.0000/70.0000; no manual metric echo; two genuine 2026 runs split 100 EUR into 30/70, 365/365; four explicit coverage reviews; immutable finalized snapshots without live suggestions; unchanged leases and histories; exact UI write whitelist');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => {
    clearTimeout(hardTimeout);
    try { if (context) await context.close(); }
    finally { if (browser) await browser.close(); }
  });
