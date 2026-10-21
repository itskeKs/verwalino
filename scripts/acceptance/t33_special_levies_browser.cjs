#!/usr/bin/env node
/* T33 acceptance: UI-only special levy, owner ledger and draft dunning.
 * Run separately after the T33 UI/backend are ready; this file starts no server/DB.
 * Synthetic records remain. No API seeding, plans, payments, issue or PDF actions.
 * JSON is observed only from UI-triggered responses. Missing labels fail closed.
 * Exact integration labels and DTO assertions are checked below; no index fallbacks.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T33 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} WEG`;
const scopeName = `${prefix} WEG-Jahreskreis`;
const CONTRACT = {
  // Coordinate these with MietkontoPage/DunningNoticesPage before the live run.
  mode: 'Ansicht', ownerMode: 'Eigentuemer (Hausgeld)', owner: 'Eigentuemer *',
  dunningMode: 'Empfaengertyp *', dunningOwnerMode: 'Eigentuemer',
  periodStart: 'Zeitraum von *', periodEnd: 'Zeitraum bis *', ledgerLoad: 'Abrufen',
  dunningNew: '+ Neue Mahnung', dunningOwner: 'Eigentuemer *',
  dunningProperty: 'WEG-Objekt *', referenceDate: 'Stichtag',
  dunningStart: 'Zeitraum von *', dunningEnd: 'Zeitraum bis *',
  dunningCreate: 'Mahnung erstellen',
};
const button = (root, name) => root.getByRole('button', { name, exact: true });
const field = (root, name) => root.getByLabel(name, { exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
let browser;
let deadline;
async function responseFor(page, predicate, action) {
  const [response] = await Promise.all([page.waitForResponse(predicate), action()]);
  return response;
}
async function successfulJson(response, status = 200) {
  if (response.status() !== status) {
    assert.fail(`${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  }
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
async function selectLabel(page, label, text) {
  const select = field(page, label);
  await select.getByRole('option', { name: text, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label: text });
}
async function chooseId(page, label, id) {
  const select = field(page, label);
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${label}: missing observed ID ${id}`));
  await select.selectOption(id);
}
function money(text) {
  const value = text.trim().replace(/\s*(?:EUR|€)$/, '').replace(/[\s\u00a0\u202f]/g, '');
  if (/^-?\d+\.\d{2}$/.test(value)) return value;
  assert.match(value, /^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}$/, `Unknown money: ${text}`);
  return value.replace(/\./g, '').replace(',', '.');
}
const cents = text => BigInt(money(text).replace('.', ''));
function decimal(value, expected) {
  assert.match(String(value), /^\d+(?:\.\d+)?$/);
  const [whole, fraction = ''] = String(value).split('.');
  assert.equal(whole.replace(/^0+(?=\d)/, '') + (fraction.replace(/0+$/, '') ? `.${fraction.replace(/0+$/, '')}` : ''), expected);
}
(async () => {
  deadline = setTimeout(() => {
    console.error('T33 failed: eight-minute acceptance deadline exceeded');
    process.exitCode = 1;
    if (browser) browser.close().catch(error => console.error('Deadline cleanup:', error));
  }, 8 * 60 * 1000);
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], mutations = [], allowedWrites = [], authResponses = [], authConsoles = [];
  let authPhase = 'anonymous';
  const sessions = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) authConsoles.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  // Abort unapproved mutations BEFORE they reach the backend. Each UI save has
  // one single-use permit; even an automatic duplicate POST is forbidden.
  await context.route('**/api/**', async route => {
    const request = route.request();
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method())) return route.continue();
    const url = new URL(request.url());
    const permit = allowedWrites.find(item => !item.used && item.method === request.method()
      && item.pathname === url.pathname && item.origins.has(url.origin));
    mutations.push(request);
    if (!permit) {
      errors.push(`Forbidden mutation: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    permit.used = true;
    return route.continue();
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) authResponses.push(response.url());
    else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  const assertClean = () => assert.deepEqual(errors, [], 'No unexpected HTTP/page/console errors or mutations');
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  page.setDefaultNavigationTimeout(30000);
  async function observe(method, pathname, action, status = 200) {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) allowedWrites.push({ method, pathname, used: false,
      origins: new Set([base, 'http://127.0.0.1:18000']) });
    const response = await responseFor(page, isApi(method, pathname), action);
    const json = await successfulJson(response, status);
    assertClean();
    return { response, json };
  }
  async function searchList(label) {
    await field(page, label).fill(prefix);
    await button(page.getByRole('search'), 'Suchen').click();
  }
  async function selectScope(id, root = page) {
    await field(root, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(root, 'Abrechnungskreis', `${scopeName} · WEG · 2026-01-01 – offen`);
    assert.equal(await field(root, 'Abrechnungskreis').inputValue(), id);
  }
  // Never load Dashboard: unrelated T42 errors are not waived.
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
  await field(page, 'Strasse').fill('Synthetischer Sonderumlagenweg 33');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T33 synthetische Sonderumlage im isolierten Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'WEG');
  await searchList('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  await field(page, 'Nenner *').fill('1000');
  const { json: scale } = await observe('POST', `/api/properties/${property.id}/mea-scale/confirm`,
    () => button(page, 'MEA-Skala bestätigen').click());
  assert.ok(scale, 'MEA confirmation returns persisted state');
  const fixtures = [];
  for (const [index, mea, amount] of [[1, '300', '3000.00'], [2, '300', '3000.00'], [3, '400', '4000.00']]) {
    await page.goto(propertyUrl);
    await button(page, '+ Neue Einheit').click();
    const unitName = `${prefix} Einheit ${index}`;
    await field(page, 'Nummer *').fill(unitName);
    await field(page, 'Flaeche (m2)').fill('70');
    await field(page, 'MEA (Miteigentumsanteil)').fill(mea);
    const { json: unit } = await observe('POST', '/api/units', () => button(page, 'Speichern').click(), 201);
    assert.equal(unit.property_id, property.id);
    decimal(unit.mea, mea);
    await page.getByRole('link', { name: unitName, exact: true }).click();
    const unitUrl = page.url();
    const firstName = `${prefix} Person ${index}`;
    const name = `${firstName} Synthetisch`;
    await page.goto(base + '/personen');
    await button(page, '+ Neue Person').click();
    await field(page, 'Vorname').fill(firstName);
    await field(page, 'Nachname *').fill('Synthetisch');
    const { json: person } = await observe('POST', '/api/persons', () => button(page, 'Speichern').click(), 201);
    await searchList('Personen suchen');
    await page.getByRole('link', { name, exact: true }).click();
    const { json: owner } = await observe('POST', '/api/owner-profiles',
      () => button(page, 'Als Eigentuemer kennzeichnen').click(), 201);
    assert.equal(owner.person_id, person.id);
    await page.goto(unitUrl);
    await button(page, '+ Eigentum').click();
    await selectLabel(page, 'Eigentuemer *', name);
    await field(page, 'Gueltig ab *').fill('2026-01-01');
    await field(page, 'Gueltig bis').fill('');
    await field(page, 'MEA-Anteil').fill('1');
    const { json: ownership } = await observe('POST', '/api/ownership', () => button(page, 'Speichern').click(), 201);
    assert.equal(ownership.unit_id, unit.id);
    assert.equal(ownership.owner_profile_id, owner.id);
    assert.equal(ownership.valid_from, '2026-01-01');
    assert.equal(ownership.valid_to, null);
    decimal(ownership.mea_share, '1');
    fixtures.push({ unit, unitName, unitUrl, person, owner, name, ownership, mea, amount });
  }
  await page.goto(base + '/berechtigungen');
  const group = page.getByRole('group', { name: 'Objekt', exact: true });
  await field(group, 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName);
  await field(page, 'Bezeichnung').fill(scopeName);
  await field(page, 'Verwaltungsart').selectOption({ label: 'WEG' });
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await observe('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click(), 201);
  assert.equal(scope.property_id, property.id);
  assert.equal(scope.scope_type, 'WEG');
  assert.equal(scope.whole_property, true);
  assert.equal(scope.valid_from, '2026-01-01');
  assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await chooseId(page, 'Teammitglied', principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten', 'Bestätigen']) await field(grantForm, label).check();
  for (const label of ['Freigeben', 'Exportieren', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T33 eigene WEG-Lese-/Vorbereitungs-/Bestätigungsrechte; keine Admin-Ersatzberechtigung, kein Versand/Export');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['confirm', 'prepare', 'read']);

  await page.goto(base + '/sonderumlagen');
  await page.getByRole('heading', { name: 'Sonderumlagen', exact: true }).waitFor();
  await selectLabel(page, 'WEG-Objekt *', propertyName);
  const levyFilters = page.locator('div.card').filter({ has: field(page, 'WEG-Objekt *') });
  await selectScope(scope.id, levyFilters);
  const levyForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Sonderumlage anlegen', exact: true }) });
  await selectScope(scope.id, levyForm);
  await field(page, 'Fällig am *').fill('2026-06-15');
  await field(page, 'Betrag (EUR) *').fill('0');
  // Native invalid form: no write permit exists, so even a faulty submit cannot
  // mutate. Test only when the actual amount control declares zero invalid.
  const zeroInvalid = await field(page, 'Betrag (EUR) *').evaluate(input => !input.checkValidity());
  const beforeZero = mutations.length;
  await button(page, 'Sonderumlage anlegen').click();
  if (!zeroInvalid) await page.getByRole('alert').filter({ hasText: 'Betrag muss positiv sein' }).waitFor();
  await page.waitForTimeout(250);
  assert.equal(mutations.length, beforeZero, 'Invalid zero amount must not POST');
  assertClean();
  await field(page, 'Betrag (EUR) *').fill('10000.00');
  const { response: levyResponse, json: levy } = await observe('POST', '/api/special-levies',
    () => button(page, 'Sonderumlage anlegen').click(), 201);
  const levyPayload = levyResponse.request().postDataJSON();
  assert.equal(levyPayload.property_id, property.id);
  assert.equal(levyPayload.accounting_scope_id, scope.id);
  assert.equal(levyPayload.amount, '10000.00');
  assert.equal(levyPayload.due_date, '2026-06-15');
  assert.ok(levyPayload.resolution_id == null, 'No fabricated resolution');
  function assertLevy(value) {
    assert.equal(value.id, levy.id);
    assert.equal(value.property_id, property.id);
    assert.equal(value.accounting_scope_id, scope.id);
    assert.equal(value.amount, '10000.00');
    assert.equal(value.due_date, '2026-06-15');
    assert.equal(value.resolution_id, null);
    assert.equal(value.allocations.length, 3);
    assert.equal(value.allocations.reduce((sum, row) => sum + cents(row.amount), 0n), 1000000n);
    for (const item of fixtures) {
      const rows = value.allocations.filter(row => row.unit_id === item.unit.id && row.owner_profile_id === item.owner.id);
      assert.equal(rows.length, 1);
      assert.equal(rows[0].amount, item.amount);
      decimal(rows[0].mea, item.mea);
    }
  }
  async function assertAllocationUI() {
    for (const item of fixtures) {
      const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: item.name, exact: true }) })
        .filter({ has: page.getByRole('cell', { name: `Wohnung ${item.unitName}`, exact: true }) });
      await row.waitFor();
      assert.equal(await row.count(), 1);
      const cells = await row.getByRole('cell').allTextContents();
      assert.ok(cells.some(text => { try { return cents(text) === cents(item.amount); } catch { return false; } }), `${item.name}: named amount missing`);
    }
    assertClean();
  }
  assertLevy(levy);
  await assertAllocationUI();
  // Reload must observe a genuinely fresh GET and retain the exact complete DTO.
  await page.reload();
  await selectLabel(page, 'WEG-Objekt *', propertyName);
  const filters = page.locator('div.card').filter({ has: field(page, 'WEG-Objekt *') });
  const persistedResponse = await responseFor(page, isApi('GET', '/api/special-levies'),
    () => selectScope(scope.id, filters));
  const persistedLevies = await successfulJson(persistedResponse);
  const persistedUrl = new URL(persistedResponse.url());
  assert.equal(persistedUrl.searchParams.get('accounting_scope_id'), scope.id);
  assert.equal(persistedUrl.searchParams.get('property_id'), property.id);
  const matchingLevies = persistedLevies.filter(value => value.id === levy.id);
  assert.equal(matchingLevies.length, 1);
  assertLevy(matchingLevies[0]);
  assert.deepEqual(matchingLevies[0], levy);
  await assertAllocationUI();

  const first = fixtures[0];
  await page.goto(base + '/mietkonto');
  await selectLabel(page, CONTRACT.mode, CONTRACT.ownerMode);
  await chooseId(page, CONTRACT.owner, first.owner.id);
  await field(page, CONTRACT.periodStart).fill('2026-01-01');
  await field(page, CONTRACT.periodEnd).fill('2026-12-31');
  await selectScope(scope.id);
  const ledgerPredicate = response => response.request().method() === 'GET'
    && new URL(response.url()).pathname.startsWith('/api/payments/mietkonto/owner/');
  const { response: ledgerResponse, json: ledger } = await observe('GET', `/api/payments/mietkonto/owner/${first.owner.id}`,
    () => button(page, CONTRACT.ledgerLoad).click());
  function assertLedgerRequest(response) {
    const url = new URL(response.url());
    assert.equal(url.pathname, `/api/payments/mietkonto/owner/${first.owner.id}`);
    assert.equal(url.searchParams.get('accounting_scope_id'), scope.id);
    assert.equal(url.searchParams.get('period_start'), '2026-01-01');
    assert.equal(url.searchParams.get('period_end'), '2026-12-31');
    assert.ok(!url.searchParams.get('economic_plan_id'), 'No economic plan selected');
  }
  assertLedgerRequest(ledgerResponse);
  function assertLedger(value) {
    const rows = value.open_items.filter(row => row.payment_type === 'SONDERZAHLUNG');
    assert.equal(rows.length, 1);
    assert.equal(rows[0].soll, '3000.00');
    assert.equal(rows[0].ist, '0.00');
    assert.equal(rows[0].open_amount, '3000.00');
    assert.equal(value.soll_total, '3000.00');
    assert.equal(value.ist_total, '0.00');
    assert.equal(value.balance, '3000.00');
    assert.equal(rows[0].period_year, 2026);
    assert.equal(rows[0].period_month, 6);
  }
  async function assertLedgerUI() {
    const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Sonder-/Nachzahlung', exact: true }) });
    await row.waitFor();
    assert.equal(await row.count(), 1);
    const values = (await row.getByRole('cell').allTextContents()).flatMap(text => {
      try { return [money(text)]; } catch { return []; }
    });
    assert.deepEqual(values, ['3000.00', '0.00', '3000.00'], 'Separate Soll / Ist / Offen cells');
  }
  assertLedger(ledger);
  await assertLedgerUI();
  // Ledger filter persistence may not survive reload; reselect explicitly rather
  // than accepting an unrelated automatic GET as proof of this owner/scope.
  await page.reload();
  await selectLabel(page, CONTRACT.mode, CONTRACT.ownerMode);
  await chooseId(page, CONTRACT.owner, first.owner.id);
  await field(page, CONTRACT.periodStart).fill('2026-01-01');
  await field(page, CONTRACT.periodEnd).fill('2026-12-31');
  await selectScope(scope.id);
  const ledgerReloadResponse = await responseFor(page, ledgerPredicate,
    () => button(page, CONTRACT.ledgerLoad).click());
  assertLedgerRequest(ledgerReloadResponse);
  const ledgerReload = await successfulJson(ledgerReloadResponse);
  assertLedger(ledgerReload);
  assert.deepEqual(ledgerReload, ledger);
  await assertLedgerUI();

  await page.goto(base + '/mahnungen');
  await button(page, CONTRACT.dunningNew).click();
  await selectLabel(page, CONTRACT.dunningMode, CONTRACT.dunningOwnerMode);
  await selectLabel(page, CONTRACT.dunningProperty, propertyName);
  await chooseId(page, CONTRACT.dunningOwner, first.owner.id);
  await field(page, CONTRACT.dunningStart).fill('2026-01-01');
  await field(page, CONTRACT.dunningEnd).fill('2026-12-31');
  await field(page, CONTRACT.referenceDate).fill('2026-07-15');
  await selectScope(scope.id);
  const { response: dunningResponse, json: notice } = await observe('POST', '/api/dunning/owner',
    () => button(page, CONTRACT.dunningCreate).click(), 201);
  const dunningPayload = dunningResponse.request().postDataJSON();
  assert.equal(dunningPayload.owner_profile_id, first.owner.id);
  assert.equal(dunningPayload.accounting_scope_id, scope.id);
  assert.equal(dunningPayload.period_start, '2026-01-01');
  assert.equal(dunningPayload.period_end, '2026-12-31');
  assert.equal(dunningPayload.reference_date, '2026-07-15');
  assert.ok(dunningPayload.economic_plan_id == null, 'Dunning must not require a fake plan');
  function assertNotice(value) {
    assert.equal(value.id, notice.id);
    assert.equal(value.status, 'draft');
    assert.equal(value.owner_profile_id, first.owner.id);
    assert.equal(value.accounting_scope_id, scope.id);
    assert.equal(value.principal_amount, '3000.00');
    const lines = value.included_items.filter(line => line.payment_type === 'SONDERZAHLUNG');
    assert.equal(lines.length, 1);
    assert.equal(lines[0].due_date, '2026-06-15');
    assert.equal(lines[0].soll, '3000.00');
    assert.equal(lines[0].ist, '0.00');
    assert.equal(lines[0].open_amount, '3000.00');
  }
  async function assertNoticeUI() {
    await page.getByRole('heading', { level: 1 }).getByText('Entwurf', { exact: true }).waitFor();
    const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Sonder-/Nachzahlung', exact: true }) });
    await row.waitFor();
    assert.ok((await row.getByRole('cell').allTextContents()).some(text => {
      try { return money(text) === '3000.00'; } catch { return false; }
    }));
    assertClean();
  }
  assertNotice(notice);
  await assertNoticeUI();
  const noticeReload = await reloadJson(page, isApi('GET', `/api/dunning/${notice.id}`));
  assertNotice(noticeReload);
  assert.deepEqual(noticeReload, notice);
  await assertNoticeUI();
  // Prove all three original full-year ownership DTOs survived unchanged.
  for (const item of fixtures) {
    await page.goto(item.unitUrl);
    const history = await reloadJson(page, response => isApi('GET', '/api/ownership')(response)
      && new URL(response.url()).searchParams.get('unit_id') === item.unit.id);
    const normalized = row => ({ ...row, mea_share: row.mea_share == null ? null : String(row.mea_share).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') });
    assert.deepEqual(history.map(normalized), [normalized(item.ownership)]);
  }
  authPhase = 'logging-out';
  allowedWrites.push({ method: 'POST', pathname: '/api/auth/logout', used: false,
    origins: new Set([base, 'http://127.0.0.1:18000']) });
  const logoutResponse = await responseFor(page, isApi('POST', '/api/auth/logout'),
    () => button(page, 'Abmelden').click());
  assert.ok([200, 204].includes(logoutResponse.status()), 'Logout succeeds');
  await button(page, 'Anmelden').waitFor();
  await context.close();
  const count = pathname => mutations.filter(request => request.method() === 'POST'
    && new URL(request.url()).pathname === pathname).length;
  assert.equal(count('/api/ownership'), 3);
  assert.equal(count('/api/special-levies'), 1);
  assert.equal(count('/api/dunning/owner'), 1);
  assert.equal(count('/api/properties'), 1);
  assert.equal(count('/api/units'), 3);
  assert.equal(count('/api/persons'), 3);
  assert.equal(count('/api/owner-profiles'), 3);
  assert.ok(allowedWrites.every(item => item.used), 'Every exact single-use write permit consumed');
  assert.deepEqual([...authResponses].sort(), [...authConsoles].sort(), 'Exact anonymous/logout session-401 pairing');
  assertClean();
  console.log('PASS: T33 UI-only levy 10000.00, named shares 3000/3000/4000, unchanged ownership, no-plan owner ledger and genuine overdue draft persisted.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => {
    clearTimeout(deadline);
    const cleanupDeadline = setTimeout(() => {
      console.error('T33 failed: browser cleanup exceeded ten seconds');
      process.exit(1);
    }, 10000);
    try { if (browser) await browser.close(); }
    finally { clearTimeout(cleanupDeadline); }
  });
