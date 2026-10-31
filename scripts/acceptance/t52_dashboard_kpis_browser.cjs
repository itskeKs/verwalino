#!/usr/bin/env node
/* T52: Dashboard zeigt erwartete und eingegangene Miete.
 * Parent runs this ONLY after backend tests, using scripts/dev-local.sh with
 * isolated temporary test data (:13000/:18000). Starts no DB/server itself.
 * Synthetic setup and booking ONLY through actual UI controls; no SQL, fixtures,
 * request clients, direct API writes, exports, external network or output files.
 * T49 supplies the stable setup aliases; T50 supplies single-use write permits
 * and the loopback-only resource guard (local Vite HMR is the sole WebSocket).
 * Authored without executing commands, DB, server or browser. Parent owns run.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T52 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} Mietobjekt`;
const scopeName = `${prefix} Mietkreis`;
const unitName = `${prefix} Wohnung belegt`;
const vacantName = `${prefix} Wohnung leer`;
const bankName = `${prefix} Laufendes Konto`;
const firstName = `${prefix} Ada`, lastName = 'Kennzahlenmieter';
const personName = `${firstName} ${lastName}`;
const field = (scope, name) => ['Objekt für Mietkennzahlen', 'Mietmonat', 'Abrechnungskreis'].includes(name)
  ? scope.getByRole('combobox', { name, exact: true })
  : scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
const sorted = values => {
  assert.ok(Array.isArray(values), 'Canonical list GET must return an array');
  return [...values].sort((a, b) => a.id.localeCompare(b.id));
};
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
// Only requests started after the replacement main document committed count.
async function freshReload(page, predicates) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation); page.on('request', started);
  try {
    const pending = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const result = await Promise.all([...pending, page.reload()]);
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
  const context = await browser.newContext({ serviceWorkers: 'block',
    viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], downloads = [], kpiRequests = [];
  const scopeChoices = [];
  let authPhase = 'anonymous', pendingWrite = null, kpiPermit = null;
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
    if (url.pathname.startsWith('/api/reports/')) {
      kpiRequests.push(request);
      const permit = kpiPermit;
      let matches = permit && !permit.seen && request.method() === 'GET'
        && url.origin === base && url.pathname === '/api/reports/dashboard-kpis';
      if (matches) {
        try { assert.deepEqual([...url.searchParams].sort(), permit.query); }
        catch (error) { matches = false; errors.push(`Unexpected KPI query: ${error.message}`); }
      }
      if (!matches) {
        errors.push(`Unexpected report request: ${request.method()} ${request.url()}`);
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
    pendingWrite.seen = true;
    return route.continue();
  });
  assert.equal(typeof context.routeWebSocket, 'function', 'Playwright must support WebSocket blocking');
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
      && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
      socket.connectToServer(); // Local Vite HMR only; never bank/public WebSockets.
      return;
    }
    errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
  });
  const assertClean = () => {
    assert.deepEqual(errors, [], 'No unexpected network/console/page errors or writes');
    assert.equal(downloads.length, 0, 'Dashboard must not download or export');
  };
  const page = await context.newPage();
  async function uiWrite(method, pathname, action, status = 201) {
    assert.equal(pendingWrite, null);
    const permit = { key: `${method} ${pathname}`, seen: false };
    pendingWrite = permit; allowedWrites.push(permit.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(permit.seen, true, 'Actual UI must consume the single-use write permit');
      const json = await successfulJson(response, status);
      assertClean();
      return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }
  await page.goto(`${base}/objekte`);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN'); authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Kennzahlenweg 52'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T52 synthetische lokale KPI-Abnahme mit ausdrücklichen Quellrechten');
  const { json: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties',
    () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyPath = `/objekte/${property.id}`;
  assert.equal(new URL(page.url()).pathname, propertyPath);
  const units = [];
  for (const name of [unitName, vacantName]) {
    await button(page, '+ Neue Einheit').click();
    await field(page, 'Nummer *').fill(name); await field(page, 'Flaeche (m2)').fill('70');
    const { json: unit } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
    assert.equal(unit.property_id, property.id); units.push(unit);
    await page.getByRole('link', { name, exact: true }).waitFor();
  }
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitPath = new URL(page.url()).pathname;
  assert.ok(unitPath.endsWith(`/${units[0].id}`));
  await page.goto(`${base}/personen`); await button(page, '+ Neue Person').click();
  await field(page, 'Vorname').fill(firstName); await field(page, 'Nachname *').fill(lastName);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  await searchList(page, 'Personen suchen');
  await page.getByRole('link', { name: personName, exact: true }).click();
  const personUrl = page.url();
  await page.goto(`${base}/kontaktdaten-rechte`);
  await field(page, 'Kontakt suchen').fill(firstName); await selectId(page, 'Kontakt', person.id);
  await selectLabel(page, 'Begründung', 'Ersteinrichtung');
  const { json: domain } = await uiWrite('POST', '/api/person-data-domains',
    () => button(page, 'Datenbereich vorbereiten').click());
  assert.equal(domain.person_id, person.id);
  await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  await field(page, 'Bearbeiten').check(); await field(page, 'Exportieren').check();
  await selectLabel(page, 'Begründung', 'Teamzugriff');
  const { json: contactGrant } = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
    () => button(page, 'Teamfreigabe vorbereiten').click());
  assert.equal(contactGrant.user_id, principal.user_id);
  assert.deepEqual([...contactGrant.capabilities].sort(), ['export', 'prepare', 'read']);
  await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
  await page.goto(personUrl);
  const { json: tenant } = await uiWrite('POST', '/api/tenant-profiles',
    () => button(page, 'Als Mieter kennzeichnen').click());
  assert.equal(tenant.person_id, person.id);
  await page.goto(base + unitPath); await button(page, '+ Mietvertrag').click();
  await selectId(page, 'Mieter *', tenant.id);
  for (const [label, value] of [['Vertragsbeginn *', '2026-10-01'], ['Vertragsende', ''],
    ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '150.00']]) await field(page, label).fill(value);
  const { json: lease } = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click());
  assert.equal(lease.unit_id, units[0].id); assert.equal(lease.tenant_profile_id, tenant.id);
  assert.equal(lease.valid_from, '2026-10-01'); assert.equal(lease.valid_to, null);
  assert.equal(lease.base_rent, '700.00'); assert.equal(lease.advance_payment, '150.00');

  await page.goto(`${base}/berechtigungen`);
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName); await field(page, 'Bezeichnung').fill(scopeName);
  await selectLabel(page, 'Verwaltungsart', 'MIET'); await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill(''); await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await uiWrite('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click());
  assert.equal(scope.property_id, property.id); assert.equal(scope.scope_type, 'MIET');
  assert.equal(scope.whole_property, true); assert.equal(scope.valid_from, '2026-01-01'); assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de'); await selectId(page, 'Teammitglied', principal.user_id);
  // Existing T49 finance workflow calls the approval capability "confirm".
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen', 'Exportieren']) await field(grantForm, label).check();
  for (const label of ['Freigeben', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T52 Lesen, Vorbereiten und Bestätigen; Exportrecht nur wie bestehender UI-Setup, kein Export oder Versand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id); assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['confirm', 'export', 'prepare', 'read']);
  async function selectScope(container = page) {
    await field(container, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(container, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(container, 'Abrechnungskreis').inputValue(), scope.id);
  }
  await page.goto(`${base}/bankkonten`); await button(page, '+ Neues Bankkonto').click();
  await selectLabel(page, 'Objekt *', propertyName); await selectLabel(page, 'Kontotyp *', 'Laufendes Konto');
  await field(page, 'Bezeichnung *').fill(bankName);
  await field(page, 'IBAN').fill('DE89370400440532013000'); // Public synthetic test vector, never transmitted.
  await selectScope();
  const { json: bank, payload: bankPayload } = await uiWrite('POST', '/api/bank-accounts', () => button(page, 'Speichern').click());
  assert.equal(bank.property_id, property.id); assert.equal(bank.account_type, 'LAUFEND');
  assert.equal(bankPayload.accounting_scope_id, scope.id);
  const payments = [];
  for (const [type, label, direction, amount, date] of [
    ['KALTMIETE', 'Kaltmiete', 'IN', '700.00', '2026-11-05'],
    ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', 'IN', '150.00', '2026-11-06'],
    ['KALTMIETE', 'Kaltmiete', 'OUT', '50.00', '2026-11-07'],
  ]) {
    await page.goto(`${base}/zahlungen`); await button(page, '+ Neue Zahlung').click();
    await selectId(page, 'Bankkonto *', bank.id); await selectLabel(page, 'Zahlungstyp *', label);
    await selectLabel(page, 'Richtung *', direction === 'IN' ? 'Eingang (Einzahlung)' : 'Ausgang (Auszahlung)');
    await field(page, 'Betrag *').fill(amount); await field(page, 'Wertstellungsdatum *').fill(date);
    await selectId(page, 'Mieter', tenant.id); await selectId(page, 'Mietvertrag', lease.id);
    await field(page, 'Periode Jahr').fill('2026'); await field(page, 'Periode Monat').fill('10');
    const { json: draft, payload } = await uiWrite('POST', '/api/payments', () => button(page, 'Anlegen').click());
    function checkPayment(value, status) {
      for (const [key, expected] of Object.entries({ bank_account_id: bank.id, property_id: property.id,
        tenant_profile_id: tenant.id, lease_agreement_id: lease.id, payment_type: type, direction, amount,
        value_date: date, period_year: 2026, period_month: 10, status, reverses_id: null, reversed_by_id: null })) {
        assert.equal(value[key], expected, key);
      }
    }
    assert.equal(payload.period_year, 2026); assert.equal(payload.period_month, 10); checkPayment(draft, 'draft');
    await page.waitForURL(`${base}/zahlungen/${draft.id}`); await button(page, 'Buchen').waitFor();
    let accepted = false;
    page.once('dialog', async dialog => {
      assert.equal(dialog.type(), 'confirm'); assert.match(dialog.message(), /^Zahlung buchen\?/);
      accepted = true; await dialog.accept();
    });
    const { json: booked } = await uiWrite('POST', `/api/payments/${draft.id}/book`, () => button(page, 'Buchen').click(), 200);
    assert.equal(accepted, true); assert.equal(booked.id, draft.id); assert.ok(booked.booked_at);
    checkPayment(booked, 'booked'); payments.push(booked);
  }
  async function readDetail(pathname, uiPath) {
    await page.goto(base + uiPath);
    const [value] = await freshReload(page, [isApi('GET', pathname)]);
    assertClean(); return value;
  }
  async function businessSnapshot() {
    await page.goto(`${base}/zahlungen`);
    const paths = ['/api/payments', '/api/bank-accounts', '/api/properties', '/api/leases'];
    const lists = await freshReload(page, paths.map(pathname => response =>
      isApi('GET', pathname)(response) && !new URL(response.url()).search));
    const result = Object.fromEntries(paths.map((pathname, i) => [pathname, sorted(lists[i])]));
    // List DTOs may deliberately project fields. Compare full lists before/after,
    // but never assume they equal creation/detail DTOs or contain only new IDs.
    for (const payment of payments) assert.ok(result['/api/payments'].some(value => value.id === payment.id));
    assert.ok(result['/api/leases'].some(value => value.id === lease.id));
    assert.ok(result['/api/bank-accounts'].some(value => value.id === bank.id));
    assert.ok(result['/api/properties'].some(value => value.id === property.id));
    result.paymentDetails = [];
    for (const payment of result['/api/payments']) result.paymentDetails.push(
      await readDetail(`/api/payments/${payment.id}`, `/zahlungen/${payment.id}`));
    for (const payment of payments) assert.deepEqual(result.paymentDetails.find(value => value.id === payment.id), payment);
    await page.goto(base + unitPath);
    const [leases, history] = await freshReload(page, [response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === units[0].id,
    isApi('GET', `/api/leases/${lease.id}/rent-changes`)]);
    assert.ok(leases.some(value => value.id === lease.id)); assert.deepEqual(history, []);
    result.unitLeases = sorted(leases); result.history = history;
    await page.goto(base + propertyPath);
    const [propertyDetail, propertyUnits] = await freshReload(page, [isApi('GET', `/api/properties/${property.id}`),
      response => isApi('GET', '/api/units')(response) && new URL(response.url()).searchParams.get('property_id') === property.id]);
    result.property = propertyDetail; result.units = sorted(propertyUnits);
    assert.deepEqual(result.units.map(value => value.id).sort(), units.map(value => value.id).sort());
    assertClean(); return result;
  }
  const before = await businessSnapshot();
  const writesBeforeDashboard = [...actualWrites];
  const kpis = () => page.getByRole('region', { name: 'Mietkennzahlen', exact: true });
  async function configureDashboard(month) {
    const ui = kpis(); await ui.waitFor();
    await selectId(ui, 'Objekt für Mietkennzahlen', property.id);
    await field(ui, 'Mietjahr').fill('2026'); await field(ui, 'Mietmonat').selectOption(String(month));
    const scopeResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return isApi('GET', '/api/accounting-scopes/page')(response)
        && url.searchParams.get('property_id') === property.id && url.searchParams.get('capability') === 'read'
        && url.searchParams.get('financial_creation') === 'true' && url.searchParams.get('q') === scopeName;
    });
    await field(ui, 'Abrechnungskreis suchen').fill(scopeName);
    await successfulJson(await scopeResponse);
    await selectLabel(ui, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(ui, 'Abrechnungskreis').inputValue(), scope.id);
  }
  async function loadKpis(month, received, arrears) {
    const ui = kpis(); assert.equal(await field(ui, 'Mietmonat').inputValue(), String(month));
    assert.equal(await field(ui, 'Mietjahr').inputValue(), '2026');
    assert.equal(await field(ui, 'Objekt für Mietkennzahlen').inputValue(), property.id);
    assert.equal(await field(ui, 'Abrechnungskreis').inputValue(), scope.id);
    assert.equal(pendingWrite, null); assert.equal(kpiPermit, null);
    const query = Object.entries({ property_id: property.id, year: '2026', month: String(month), accounting_scope_id: scope.id }).sort();
    const permit = { query, seen: false }; kpiPermit = permit;
    let value;
    try {
      const [response] = await Promise.all([page.waitForResponse(response => {
        const url = new URL(response.url());
        return isApi('GET', '/api/reports/dashboard-kpis')(response)
          && JSON.stringify([...url.searchParams].sort()) === JSON.stringify(query);
      }), button(ui, 'Mietkennzahlen laden').click()]);
      assert.equal(permit.seen, true); value = await successfulJson(response);
    } finally { kpiPermit = null; }
    const end = month === 10 ? '2026-10-31' : '2026-11-30';
    assert.deepEqual(value, {
      property_id: property.id, property_name: propertyName, year: 2026, month,
      period_start: month === 10 ? '2026-10-01' : '2026-11-01', period_end: end,
      accounting_scope_id: scope.id, occupancy_date: end, payment_basis: 'assigned_rent_period',
      expected_rent: '850.00', received_rent: received, arrears, arrears_count: 1,
      unit_count: 2, occupied_units: 1, vacant_units: 1, unassigned_rent_count: 0,
      unassigned_rent_amount: '0.00', footnote: value.footnote,
    });
    assert.equal(typeof value.footnote, 'string'); assert.ok(value.footnote.trim());
    for (const [label, amount] of [['Soll-Miete', '850,00 €'], ['Ist-Miete', month === 10 ? '800,00 €' : '0,00 €'],
      ['Mietrückstände', month === 10 ? '50,00 €' : '900,00 €']]) {
      await ui.getByRole('group', { name: label, exact: true }).getByText(amount, { exact: true }).waitFor();
    }
    await ui.getByText('Anzahl positive Mietkonten: 1', { exact: true }).waitFor();
    await ui.getByText(`Einheiten: 2 · Belegt: 1 · Leer: 1 · Belegungsstichtag: ${end.split('-').reverse().join('.')}`, { exact: true }).waitFor();
    await ui.getByText(`${propertyName} · Mietzeitraum ${month === 10 ? '01.10.2026 – 31.10.2026' : '01.11.2026 – 30.11.2026'}`, { exact: true }).waitFor();
    await ui.getByText('Kaltmiete + Nebenkosten nach dem zugeordneten Mietmonat; nicht nach Wertstellung oder Buchungsdatum.', { exact: true }).waitFor();
    await ui.getByText('Leerstand: aktueller Einheitenbestand, geprüft zum ausgewählten Monatsende.', { exact: true }).waitFor();
    assert.equal(await ui.getByRole('alert').count(), 0); assertClean();
    assert.deepEqual(actualWrites, writesBeforeDashboard, 'Dashboard must be read-only');
    return value;
  }
  await page.getByRole('link', { name: 'Dashboard', exact: true }).click();
  await configureDashboard(10);
  const october = await loadKpis(10, '800.00', '50.00');
  const countBeforeMonthChange = kpiRequests.length;
  await field(kpis(), 'Mietmonat').selectOption('11');
  await eventually(async () => assert.equal(await kpis().getByRole('group', { name: 'Ist-Miete', exact: true }).count(), 0,
    'Month changes must clear stale KPI cards before explicit load'));
  assert.equal(kpiRequests.length, countBeforeMonthChange, 'Month selection alone must not issue a report');
  await loadKpis(11, '0.00', '900.00');
  await field(kpis(), 'Mietmonat').selectOption('10');
  await eventually(async () => assert.equal(await kpis().getByRole('group', { name: 'Ist-Miete', exact: true }).count(), 0));
  assert.deepEqual(await loadKpis(10, '800.00', '50.00'), october);
  await page.reload(); // Real document reload, select the property/scope again.
  await configureDashboard(10);
  assert.deepEqual(await loadKpis(10, '800.00', '50.00'), october);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload(); // Actual phone document and new KPI GET, not a resized old result.
  await configureDashboard(10);
  assert.deepEqual(await loadKpis(10, '800.00', '50.00'), october);
  await kpis().scrollIntoViewIfNeeded();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(overflow, false, '390px phone dashboard must not overflow horizontally');
  assert.deepEqual(await businessSnapshot(), before,
    'Dashboard reads leave complete canonical leases/history/payments/banks/units/property GETs exactly unchanged');
  assert.deepEqual(actualWrites, writesBeforeDashboard);
  assert.deepEqual(actualWrites, allowedWrites, 'Only explicit single-use UI setup/booking writes');
  assert.equal(actualWrites.length, 18, 'Exactly login, property, 2 units, contact/domain/grant/profile, lease, scope/grant, bank and 3 create/book pairs');
  assert.equal(kpiRequests.length, 5, 'October, November, October, desktop reload, phone reload');
  const propertyReads = scopeChoices.filter(url => url.searchParams.get('property_id') === property.id
    && url.searchParams.get('capability') === 'read' && url.searchParams.get('q') === scopeName);
  assert.ok(propertyReads.length >= 3, 'Each dashboard document uses property-filtered READ choices');
  assertClean();
  console.log('T52 UI PASS: Dashboard zeigt erwartete und eingegangene Miete; October 850/800/50, one arrears account; 2 units, 1 occupied/1 vacant at 31.10.2026; November 850/0/900; assigned-month basis despite November value dates; October return/reloads; identical canonical business GET snapshots; real 390x844 reload. No export/external network.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
