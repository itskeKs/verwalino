#!/usr/bin/env node
/* T28: real human UI setup, confirmed annual BK costs and explicit paid zero;
 * finalized advance proposal accepted ONCE, append-only history, unchanged lease
 * and final snapshot, and actual 2027 ledger before/after. Root alone starts the
 * isolated UI13000/API18000 after green tests. No server, API seeding, SQL,
 * downloads, workspace fixtures/reports or business writes outside the UI.
 * Run: node scripts/acceptance/t28_advance_proposal_browser.cjs
 * Missing accessible labels/business prerequisites fail closed. Synthetic data remain.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T28 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} Mietobjekt`;
const scopeName = `${prefix} Mietkreis`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
const receiptNumber = `${prefix} Rechnung`;
const filename = `${prefix.replace(/ /g, '-')}.pdf`;
function receiptPdf() {
  const text = [
    'T28 SYNTHETIC INVOICE - NOT A REAL CLAIM',
    `Receipt ${receiptNumber}`,
    'Supplier: Synthetic Property Tax Fixture',
    'Invoice date: 2026-12-15',
    'Service period: 2026-01-01 to 2026-12-31',
    'Annual property tax: 1200.00 EUR',
    'VAT: 0.00 EUR; gross total: 1200.00 EUR',
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


let browser, context;
const hardTimeout = setTimeout(() => {
  console.error('FAIL T28: ten-minute hard timeout');
  process.exitCode = 1;
  void (async () => {
    try { if (context) await context.close(); }
    finally { if (browser) await browser.close(); }
  })().finally(() => process.exit(1));
  setTimeout(() => process.exit(1), 5000).unref();
}, 600000);

async function responseFor(page, predicate, action) {
  const [response] = await Promise.all([page.waitForResponse(predicate), action()]);
  return response;
}
async function successfulJson(response, expected = 200) {
  if (response.status() !== expected) assert.fail(
    `${response.request().method()} ${response.url()}: expected ${expected}, got ${response.status()}: ${await response.text()}`);
  return response.json();
}
// Only responses to requests started AFTER this reload's main-frame commit count.
async function reloadJson(page, predicate) {
  let committed = false;
  const freshRequests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) freshRequests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    return await successfulJson(await responseFor(page, response => predicate(response)
      && freshRequests.has(response.request()), () => page.reload()));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
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
async function selectLabel(scope, label, optionLabel) {
  const select = field(scope, label);
  await select.getByRole('option', { name: optionLabel, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label: optionLabel });
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: false });
  const errors = [], mutations = [], session401 = [], console401 = [];
  let authPhase = 'anonymous';
  const sessions = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('download', () => errors.push('Unexpected download'));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) console401.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) {
      session401.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  context.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')
      && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request);
  });
  const clean = () => assert.deepEqual(errors, [], 'Unexpected HTTP/page/console error (no general 4xx exceptions)');
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.setDefaultNavigationTimeout(30000);
  async function observe(method, pathname, action, expected = 200) {
    const response = await responseFor(page, isApi(method, pathname), action);
    const json = await successfulJson(response, expected);
    clean();
    return { response, json };
  }
  async function search(label) {
    await field(page, label).fill(prefix);
    await button(page.getByRole('search'), 'Suchen').click();
  }
  async function propertySelector(label) {
    const group = page.getByRole('group', { name: label, exact: true });
    await field(group, 'Objekte durchsuchen').fill(propertyName);
    await selectLabel(page, label, propertyName);
  }

  // /objekte is deliberately the initial AND post-login destination, not /.
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
  const management = field(page, 'Verwaltungsart *');
  const managementOptions = await management.getByRole('option').evaluateAll(options =>
    options.map(option => ({ value: option.value, label: option.label })));
  const mietOptions = managementOptions.filter(option => option.value === 'MIET');
  assert.equal(mietOptions.length, 1, 'Known MIET option must have one live human label');
  assert.ok(mietOptions[0].label.trim());
  await selectLabel(page, 'Verwaltungsart *', mietOptions[0].label);
  await field(page, 'Strasse').fill('Synthetischer Vorauszahlungsweg 28');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T28 synthetische Vorauszahlungsprüfung im isolierten lokalen Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'MIET');
  await search('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  assert.ok(new URL(propertyUrl).pathname.endsWith(`/${property.id}`));

  const units = [], leases = [];
  for (const area of [60]) {
    await page.goto(propertyUrl);
    await button(page, '+ Neue Einheit').click();
    const name = `${prefix} Wohnung ${area}`;
    await field(page, 'Nummer *').fill(name);
    await field(page, 'Flaeche (m2)').fill(String(area));
    const { json: unit } = await observe('POST', '/api/units', () => button(page, 'Speichern').click(), 201);
    assert.equal(unit.property_id, property.id);
    await page.getByRole('link', { name, exact: true }).click();
    units.push({ ...unit, url: page.url() });
  }
  for (let index = 0; index < units.length; index++) {
    const firstName = `${prefix} Mieter ${index + 1}`;
    const personName = `${firstName} Synthetisch`;
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
    await page.goto(units[index].url);
    await button(page, '+ Mietvertrag').click();
    await selectLabel(page, 'Mieter *', personName);
    await field(page, 'Vertragsbeginn *').fill('2026-01-01');
    await field(page, 'Kaltmiete (EUR) *').fill('700.00');
    await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
    await field(page, 'Kaution (EUR)').fill('');
    const { json: lease } = await observe('POST', '/api/leases', () => button(page, 'Speichern').click(), 201);
    assert.equal(lease.unit_id, units[index].id);
    assert.equal(lease.tenant_profile_id, tenant.id);
    assert.equal(lease.valid_from, '2026-01-01');
    assert.equal(lease.base_rent, '700.00');
    assert.equal(lease.advance_payment, '150.00');
    assert.equal(lease.deposit, null);
    leases.push(lease);
  }
  assert.equal(leases.length, 1);
  const lease = leases[0], unit = units[0];
  const personName = `${prefix} Mieter 1 Synthetisch`;
  const changesPath = `/api/leases/${lease.id}/rent-changes`;
  const originalLease = structuredClone(lease);

  await page.goto(base + '/berechtigungen');
  await propertySelector('Objekt');
  await field(page, 'Bezeichnung').fill(scopeName);
  const scopeTypes = await field(page, 'Verwaltungsart').getByRole('option').evaluateAll(options =>
    options.map(option => ({ value: option.value, label: option.label })));
  const mietScopeTypes = scopeTypes.filter(option => option.value === 'MIET');
  assert.equal(mietScopeTypes.length, 1);
  assert.ok(mietScopeTypes[0].label.trim());
  await selectLabel(page, 'Verwaltungsart', mietScopeTypes[0].label);
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
  const form = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await form.waitFor();
  await field(form, 'Teammitglied suchen').fill('test@example.de');
  const memberSelect = field(form, 'Teammitglied');
  await eventually(async () => {
    const options = await memberSelect.getByRole('option').evaluateAll(options =>
      options.map(option => ({ value: option.value, label: option.label })));
    const matches = options.filter(option => option.value === principal.user_id);
    assert.equal(matches.length, 1);
    assert.ok(matches[0].label.includes('test@example.de'));
    await selectLabel(form, 'Teammitglied', matches[0].label);
  });
  for (const name of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Freigeben']) await field(form, name).check();
  for (const name of ['Exportieren', 'Versenden']) await field(form, name).uncheck();
  await field(form, 'Begründung').fill('T28 ausdrückliche Rechte für beleggeprüfte BK-Berechnung und Finalisierung');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(form, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'confirm', 'prepare', 'read']);

  // Native options are selected by their observed human labels, never by index
  // or a UUID fallback. IDs only establish identity of already observed records.
  async function selectObserved(label, id, humanText) {
    const control = field(page, label);
    await eventually(async () => {
      const options = await control.getByRole('option').evaluateAll(items =>
        items.map(item => ({ value: item.value, label: item.label })));
      const matches = options.filter(item => item.value === id);
      assert.equal(matches.length, 1, `${label}: expected one observed record`);
      assert.ok(matches[0].label.includes(humanText), `${label}: missing human-readable label`);
      assert.ok(!matches[0].label.includes(id), `${label}: UUID is not a human label`);
      await selectLabel(page, label, matches[0].label);
    });
  }
  async function selectScope() {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scope.id);
  }
  async function exactCells(row, values) {
    await row.waitFor();
    await eventually(async () => {
      assert.deepEqual(await row.getByRole('cell').allTextContents(), values);
    });
  }
  const unitUrl = unit.url;
  async function assertLeaseAndHistory(expectedHistory) {
    await page.goto(unitUrl);
    const history = await reloadJson(page, isApi('GET', changesPath));
    assert.deepEqual(history, expectedHistory, 'Exactly the append-only persisted history');
    const persisted = await reloadJson(page, response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id);
    assert.ok(Array.isArray(persisted));
    assert.deepEqual(persisted.filter(item => item.id === lease.id), [originalLease],
      'Original lease remains byte-for-byte JSON equal, including 700/150');
    const leaseRow = page.getByRole('row').filter({
      has: page.getByRole('cell', { name: personName, exact: true }),
    });
    await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
    await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();
    assert.equal(await leaseRow.count(), 1);
    if (expectedHistory.length) {
      const row = page.getByRole('table', { name: /^Miethistorie:/ }).getByRole('row').filter({
        has: page.getByRole('cell', { name: '2027-01-01', exact: true }),
      });
      await row.getByRole('cell', { name: '700.00', exact: true }).waitFor();
      await row.getByRole('cell', { name: '100.00', exact: true }).waitFor();
      assert.equal(await row.count(), 1);
    }
    clean();
  }
  async function ledger(advance, total) {
    await page.goto(base + '/mietkonto');
    // Recreate the complete selection after a genuine reload, not React state.
    await reloadJson(page, isApi('GET', '/api/leases'));
    await selectObserved('Mietvertrag *', lease.id, personName);
    await field(page, 'Zeitraum von *').fill('2027-01-01');
    await field(page, 'Zeitraum bis *').fill('2027-12-31');
    await selectScope();
    const { response, json } = await observe('GET', `/api/payments/mietkonto/lease/${lease.id}`,
      () => button(page, 'Abrufen').click());
    const query = new URL(response.url()).searchParams;
    assert.equal(query.get('period_start'), '2027-01-01');
    assert.equal(query.get('period_end'), '2027-12-31');
    assert.equal(json.soll_total, total);
    assert.equal(json.ist_total, '0.00');
    assert.equal(json.balance, total);
    assert.equal(json.open_items.length, 24);
    const summary = page.getByRole('table').filter({
      has: page.getByRole('cell', { name: 'Soll gesamt', exact: true }),
    });
    for (const [label, value] of [
      ['Soll gesamt', `${total} EUR`], ['Ist gesamt', '0.00 EUR'],
      ['Saldo', `${total} EUR (Rueckstand)`],
    ]) await exactCells(summary.getByRole('row').filter({
      has: page.getByRole('cell', { name: label, exact: true }),
    }), [label, value]);
    const detail = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: 'Offen', exact: true }),
    });
    for (let month = 1; month <= 12; month++) {
      const period = `2027/${String(month).padStart(2, '0')}`;
      for (const [type, label, amount] of [
        ['KALTMIETE', 'Kaltmiete', '700.00'], ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', advance],
      ]) {
        const items = json.open_items.filter(item => item.period_year === 2027
          && item.period_month === month && item.payment_type === type);
        assert.equal(items.length, 1, `${period}: exactly one ${type}`);
        assert.equal(items[0].soll, amount);
        assert.equal(items[0].ist, '0.00');
        assert.equal(items[0].open_amount, amount);
        const row = detail.getByRole('row')
          .filter({ has: page.getByRole('cell', { name: period, exact: true }) })
          .filter({ has: page.getByRole('cell', { name: label, exact: true }) });
        await exactCells(row, [period, label, `${amount} EUR`, '0.00 EUR', `${amount} EUR`]);
      }
    }
    assert.equal(await detail.locator('tbody').getByRole('row').count(), 24);
    clean();
    return json;
  }
  await assertLeaseAndHistory([]);
  await ledger('150.00', '10200.00');

  // This fixture is a valid, xref-backed PDF buffer uploaded through the real
  // human file control. No file is written and no PDF download is requested.
  await page.goto(base + '/belege');
  await field(page, 'Belegdatei').setInputFiles({ name: filename,
    mimeType: 'application/pdf', buffer: receiptPdf() });
  const { json: document } = await observe('POST', '/api/invoice-documents/upload',
    () => button(page, 'Hochladen').click(), 201);
  assert.equal(document.original_filename, filename);
  await page.getByRole('row').filter({
    has: page.getByRole('cell', { name: filename, exact: true }),
  }).getByRole('link', { name: 'Details', exact: true }).click();
  await button(page, 'Kostenposition erstellen').click();
  await selectLabel(page, 'Objekt *', propertyName);
  const categoryControl = field(page, 'Kategorie *');
  await eventually(async () => {
    const labels = await categoryControl.getByRole('option').allTextContents();
    const matches = labels.map(label => label.trim()).filter(label => /\bGrundsteuer\b/.test(label));
    assert.equal(matches.length, 1, 'Prerequisite: one real tenant-apportionable Grundsteuer category');
    await selectLabel(page, 'Kategorie *', matches[0]);
  });
  await field(page, 'Rechnungsdatum *').fill('2026-12-15');
  await field(page, 'Leistungszeitraum von *').fill('2026-01-01');
  await field(page, 'Leistungszeitraum bis *').fill('2026-12-31');
  await field(page, 'Bruttobetrag *').fill('1200.00');
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
  assert.equal(cost.gross_amount, '1200.00');
  assert.equal(cost.service_period_from, '2026-01-01');
  assert.equal(cost.service_period_to, '2026-12-31');
  await page.waitForURL(base + `/kosten/${cost.id}`);
  const { json: confirmedCost } = await observe('POST', `/api/cost-entries/${cost.id}/confirm`,
    () => button(page, 'Bestaetigen').click());
  assert.equal(confirmedCost.status, 'confirmed');
  assert.equal(confirmedCost.gross_amount, '1200.00');
  assert.deepEqual(await reloadJson(page, isApi('GET', `/api/cost-entries/${cost.id}`)), confirmedCost);
  const { json: released } = await observe('GET', `/api/invoice-documents/${document.id}`,
    () => page.goto(base + `/belege/${document.id}`));
  assert.equal(released.cost_entry_id, cost.id);
  assert.ok(released.released_at, 'Confirmation genuinely releases the supporting receipt');

  await page.goto(base + '/abrechnungen');
  await button(page, '+ Neue Abrechnung').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectObserved('Mietvertrag *', lease.id, personName);
  await field(page, 'Abrechnungszeitraum von *').fill('2026-01-01');
  await field(page, 'Abrechnungszeitraum bis *').fill('2026-12-31');
  // This synthetic tenant has made NO payments. Zero is an explicit reviewed
  // actual amount, not the contractual 12*150 and not inferred from absence.
  await field(page, 'Vorauszahlungssumme').fill('0.00');
  await selectScope();
  const { response: createResponse, json: run } = await observe('POST', '/api/settlements',
    () => button(page, 'Anlegen').click(), 201);
  assert.equal(createResponse.request().postDataJSON().accounting_scope_id, scope.id);
  assert.equal(createResponse.request().postDataJSON().advance_payments_total, '0.00');
  assert.equal(run.lease_agreement_id, lease.id);
  assert.equal(run.advance_payments_total, '0.00');
  assert.equal(run.status, 'draft');
  await page.waitForURL(base + `/abrechnungen/${run.id}`);
  const runPath = `/api/settlements/${run.id}`;
  const proposalPath = `${runPath}/advance-proposal`;
  async function coverage(reason) {
    const section = page.getByRole('region', { name: 'Datengrundlage bestätigen', exact: true });
    await field(section, 'Grundlage der Prüfung').fill(reason);
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
  await reloadJson(page, isApi('GET', runPath));
  await coverage('T28 vollständig geprüftes synthetisches Jahr 2026: ein bestätigter Jahresbeleg Grundsteuer 1200 EUR, eine ganzjährig vermietete Einheit; tatsächlich gezahlte NK-Vorauszahlungen ausdrücklich 0,00 EUR, keine Zahlungen vorhanden. Keine weiteren Kosten oder externen/manuellen Beträge.');
  const { json: computed } = await observe('POST', `${runPath}/compute`,
    () => button(page, 'Berechnen').click());
  function assertAnnual(value) {
    assert.equal(value.lease_agreement_id, lease.id);
    assert.equal(value.period_start, '2026-01-01');
    assert.equal(value.period_end, '2026-12-31');
    assert.equal(value.total_cost, '1200.00');
    assert.equal(value.advance_payments_total, '0.00');
    assert.equal(value.balance, '1200.00');
    assert.ok(value.lines.length > 0);
  }
  assertAnnual(computed);
  // Finalize from a fresh detail view with a freshly explicit completeness review.
  const computedReload = await reloadJson(page, isApi('GET', runPath));
  assertAnnual(computedReload);
  assert.deepEqual(computedReload.lines, computed.lines);
  await coverage('T28 erneute vollständige Prüfung nach Reload: ausschließlich bestätigte Jahres-Grundsteuer 1200 EUR, volles Mietjahr; tatsächliche Zahlungen ausdrücklich null, keine weiteren Eingaben.');
  const { json: finalized } = await observe('POST', `${runPath}/finalize`, async () => {
    const dialogPromise = page.waitForEvent('dialog');
    const clickPromise = button(page, 'Finalisieren').click();
    const dialog = await dialogPromise;
    assert.equal(dialog.type(), 'confirm');
    assert.equal(dialog.message(), 'Abrechnung finalisieren? Nach der Finalisierung ist die Abrechnung unveraenderbar (GoBD). Aenderungen sind nur als Korrekturabrechnung moeglich.');
    await dialog.accept();
    await clickPromise;
  });
  assertAnnual(finalized);
  assert.equal(finalized.status, 'finalized');
  assert.match(finalized.snapshot_sha256, /^[a-f0-9]{64}$/);
  const immutableFinal = structuredClone(finalized);
  assert.deepEqual(await reloadJson(page, isApi('GET', runPath)), immutableFinal);
  const proposal = await reloadJson(page, isApi('GET', proposalPath));
  function assertProposal(value) {
    assert.deepEqual(value, {
      lease_agreement_id: lease.id, tenant_cost: '1200.00', months: '12',
      monthly_amount: '100.00', period_start: '2026-01-01', period_end: '2026-12-31',
    });
  }
  assertProposal(proposal);
  const proposalRegion = page.getByRole('region', { name: 'Vorauszahlungsvorschlag', exact: true });
  await proposalRegion.waitFor();
  await eventually(async () => {
    assert.match(await proposalRegion.innerText(), /100[.,]00/);
  });
  assert.match(await proposalRegion.innerText(), /12/);
  // The complete pre-acceptance lease/history and 2027 ledger survive reload.
  await assertLeaseAndHistory([]);
  await ledger('150.00', '10200.00');
  await observe('GET', runPath, () => page.goto(base + `/abrechnungen/${run.id}`));
  assertProposal(await reloadJson(page, isApi('GET', proposalPath)));
  await field(proposalRegion, 'Übernehmen ab').fill('2027-01-01');
  const { response: acceptResponse, json: change } = await observe('POST', proposalPath,
    () => button(proposalRegion, 'Vorauszahlung übernehmen').click(), 201);
  assert.deepEqual(acceptResponse.request().postDataJSON(), { valid_from: '2027-01-01' });
  assert.ok(change.id);
  assert.equal(change.lease_agreement_id, lease.id);
  assert.equal(change.valid_from, '2027-01-01');
  assert.equal(change.base_rent, '700.00');
  assert.equal(change.advance_payment, '100.00');
  // Compare the ENTIRE finalized JSON, not merely its displayed aggregate/hash.
  assert.deepEqual(await reloadJson(page, isApi('GET', runPath)), immutableFinal);
  assertProposal(await reloadJson(page, isApi('GET', proposalPath)));
  await assertLeaseAndHistory([change]);
  await ledger('100.00', '9600.00');
  await observe('GET', runPath, () => page.goto(base + `/abrechnungen/${run.id}`));
  assert.deepEqual(await reloadJson(page, isApi('GET', runPath)), immutableFinal);
  await proposalRegion.waitFor();
  await eventually(async () => {
    assert.match(await proposalRegion.innerText(), /100[.,]00/);
  });

  // Every write must be one legal UI action, with no hidden acceptance retry,
  // direct rent-history POST, lease PATCH, bank/payment seed, or finalization fake.
  const allowed = new Map([
    ['POST /api/auth/login', 1], ['POST /api/properties', 1], ['POST /api/units', 1],
    ['POST /api/persons', 1], ['POST /api/tenant-profiles', 1], ['POST /api/leases', 1],
    ['POST /api/accounting-scopes', 1], [`POST /api/accounting-scopes/${scope.id}/grants`, 1],
    ['POST /api/invoice-documents/upload', 1], ['POST /api/cost-entries', 1],
    [`POST /api/cost-entries/${cost.id}/confirm`, 1], ['POST /api/settlements', 1],
    ['POST /api/financial-input-coverage', 2], [`POST ${runPath}/compute`, 1],
    [`POST ${runPath}/finalize`, 1], [`POST ${proposalPath}`, 1], ['POST /api/auth/logout', 1],
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
  assert.deepEqual([...actual].sort(), [...allowed].sort(), 'Only the enumerated UI writes, exactly once');
  assert.deepEqual([...session401].sort(), [...console401].sort(),
    'Exact 1:1 anonymous/logout auth/session GET401 console pairing');
  clean();
  await browser.close();
  browser = null;
  console.log('PASS T28: real UI annual tenant costs 1200 / 12 = 100; one 2027 proposal acceptance; immutable lease 700/150 and finalized snapshot/lines; one rent-history append 700/100; reloaded 2027 ledger 10200 -> 9600, paid zero, exactly 24 monthly rows');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => {
    clearTimeout(hardTimeout);
    try { if (context) await context.close(); }
    finally { if (browser) await browser.close(); }
  });
