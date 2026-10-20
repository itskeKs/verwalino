#!/usr/bin/env node
/* T25 UI-only Kaution acceptance (Referenzsoftware RF03/RF05–RF08).
 * Root starts scripts/dev-local.sh separately, only after backend/frontend tests
 * are green: UI 127.0.0.1:13000, API 18000, isolated test DB 55432; NEVER production.
 * Every master record/payment is created via UI. Responses only observe UI requests.
 * No request API, SQL, mocks, screenshots, runtime reports or original lease PATCH.
 * Requires existing Chrome/Playwright and OWNER_ADMIN test@example.de/testpassword.
 * Run: node scripts/acceptance/t25_deposits_browser.cjs
 * Synthetic data intentionally remains. Refund is mandatory, not a Storno.
 * New overview assumption: specified headings, columns and link; no wizard/labels
 * assumed. Existing payment/account form labels are sourced from their components.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T25 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const property = `${prefix} Mietobjekt`;
const account = `${prefix} Kautionskonto`;
const unit = `${prefix} Wohnung`;
const firstName = `${prefix} Ada`;
const lastName = 'Synthetisch';
const person = `${firstName} ${lastName}`;
let browser;

const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, label) => scope.getByLabel(label, { exact: true });
async function responseFor(page, predicate, action) {
  const [response] = await Promise.all([page.waitForResponse(predicate), action()]);
  return response;
}
async function successfulJson(response, expected = 200) {
  assert.equal(response.status(), expected,
    `${response.request().method()} ${response.url()}: ${await response.text()}`);
  return response.json();
}
async function reloadJson(page, predicate) {
  let documentStart = Infinity;
  const navigation = request => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      documentStart = request.timing().startTime;
    }
  };
  page.on('request', navigation);
  try {
    return await successfulJson(await responseFor(page, response => predicate(response)
      && response.request().timing().startTime >= documentStart, () => page.reload()));
  } finally {
    page.off('request', navigation);
  }
}
async function searchList(page, label, query) {
  await field(page, label).fill(query);
  await button(page.getByRole('search'), 'Suchen').click();
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
async function exactCells(row, expected) {
  await row.waitFor({ state: 'visible' });
  await eventually(async () => {
    assert.equal(await row.count(), 1, 'Exactly one matching row required');
    assert.deepEqual((await row.getByRole('cell').allTextContents()).map(text => text.trim()), expected);
  });
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], mutations = [], expectedAuth401 = [], authConsole401 = [];
  const expectedScope409 = [], scopeConsole409 = [], scopeProbeReads = [];
  const unselectedScopeRequests = new Set();
  let choosingDepositScope = false;
  let authPhase = 'anonymous';
  const sessionUrls = new Set([base + '/api/auth/session', 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', page => {
    page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && url
        && sessionUrls.has(url) && ['anonymous', 'logging-out'].includes(authPhase)) {
        authConsole401.push(url);
      } else if (/status of 409/.test(message.text()) && url
        && new URL(url).pathname === '/api/payments/deposits'
        && !new URL(url).searchParams.has('accounting_scope_id')) {
        scopeConsole409.push(url);
      } else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 409 && unselectedScopeRequests.has(response.request())) {
      expectedScope409.push(response.url());
      scopeProbeReads.push(response.json().then(body => {
        if (body.detail !== 'Select an exact accounting scope and review unclassified inputs before calculating payments') {
          errors.push(`Unexpected missing-scope response: ${JSON.stringify(body)}`);
        }
      }, error => errors.push(`Cannot verify missing-scope response: ${error.message}`)));
      return;
    }
    if (response.status() === 401 && ['anonymous', 'logging-out'].includes(authPhase)
      && response.request().method() === 'GET'
      && sessionUrls.has(response.url())) {
      expectedAuth401.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.url()}`);
  });
  context.on('request', request => {
    if (choosingDepositScope && request.method() === 'GET'
      && new URL(request.url()).pathname === '/api/payments/deposits'
      && !new URL(request.url()).searchParams.has('accounting_scope_id')) {
      unselectedScopeRequests.add(request);
    }
    if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') {
      mutations.push(request);
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  await page.goto(base + '/objekte');
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const principal = await successfulJson(await responseFor(page, response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/auth/login',
  () => button(page, 'Anmelden').click()));
  assert.equal(principal.role, 'OWNER_ADMIN');
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  authPhase = 'authenticated';

  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property);
  const management = page.getByLabel('Verwaltungsart');
  assert.equal(await management.locator('option[value="MIET"]').count(), 1, 'MIET option required for this rent journey');
  await management.selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Kautionsweg 25');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await field(page, 'Begründung der Freigabe').fill('T25 synthetische UI-Akzeptanz im isolierten lokalen Testsystem');
  await button(page, 'Speichern').click();
  await searchList(page, 'Objekte suchen', prefix);
  await page.getByRole('link', { name: property, exact: true }).click();
  const propertyId = new URL(page.url()).pathname.split('/').pop();
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unit);
  await field(page, 'Flaeche (m2)').fill('70');
  await button(page, 'Speichern').click();
  await page.getByRole('link', { name: unit, exact: true }).click();
  const unitUrl = page.url();
  const unitId = new URL(unitUrl).pathname.split('/').pop();

  await page.goto(base + '/personen');
  await button(page, '+ Neue Person').click();
  await field(page, 'Vorname').fill(firstName);
  await field(page, 'Nachname *').fill(lastName);
  await button(page, 'Speichern').click();
  await searchList(page, 'Personen suchen', prefix);
  await page.getByRole('link', { name: person, exact: true }).click();
  await button(page, 'Als Mieter kennzeichnen').click();
  await page.getByText('Mieter', { exact: true }).waitFor();
  await page.goto(unitUrl);
  await button(page, '+ Mietvertrag').click();
  const tenant = field(page, 'Mieter *');
  await tenant.getByRole('option', { name: person, exact: true }).waitFor({ state: 'attached' });
  await tenant.selectOption({ label: person });
  await field(page, 'Vertragsbeginn *').fill('2026-01-01');
  await field(page, 'Kaltmiete (EUR) *').fill('700.00');
  await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
  await field(page, 'Kaution (EUR)').fill('1800.00');
  const createdLease = await successfulJson(await responseFor(page, response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/leases',
  () => button(page, 'Speichern').click()), 201);
  const leaseId = createdLease.id;
  assert.equal(typeof leaseId, 'string');
  assert.ok(leaseId);
  assert.equal(createdLease.valid_from, '2026-01-01');
  assert.equal(createdLease.base_rent, '700.00');
  assert.equal(createdLease.advance_payment, '150.00');
  assert.equal(createdLease.deposit, '1800.00');
  const tenantId = createdLease.tenant_profile_id;
  assert.ok(tenantId);
  const leaseTable = page.getByRole('table').filter({
    has: page.getByRole('columnheader', { name: 'NK-Voraus.', exact: true }),
  });
  const leaseRow = leaseTable.getByRole('row').filter({ has: page.getByRole('cell', { name: person, exact: true }) });
  await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
  await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();

  await leaseRow.getByRole('cell', { name: '1800.00', exact: true }).waitFor();

  // Preserve exact decimal semantics; allow only established EUR/German UI formats.
  function money(text) {
    const value = text.trim().replace(/\s*(?:EUR|€)$/, '').replace(/[\s\u00a0\u202f]/g, '');
    if (/^-?\d+\.\d{2}$/.test(value)) return value;
    assert.match(value, /^-?(?:\d+|\d{1,3}(?:\.\d{3})+),\d{2}$/,
      `Unrecognized monetary presentation: ${text}`);
    return value.replace(/\./g, '').replace(',', '.');
  }
  async function moneyCells(row, names, amounts) {
    await row.waitFor();
    await eventually(async () => {
      assert.equal(await row.count(), 1);
      const values = (await row.getByRole('cell').allInnerTexts()).map(text => text.trim().replace(/\s+/g, ' '));
      assert.equal(values.length, names.length + amounts.length);
      assert.deepEqual(values.slice(0, names.length), names);
      assert.deepEqual(values.slice(names.length).map(money), amounts);
    });
  }
  const isApi = (method, path) => response => response.request().method() === method
    && new URL(response.url()).pathname === path;
  async function chooseValue(label, value) {
    const select = field(page, label);
    await select.locator(`option[value="${value}"]`).waitFor({ state: 'attached' });
    await select.selectOption(value);
  }
  async function selectProperty(label) {
    const group = page.getByRole('group', { name: label, exact: true });
    await field(group, 'Objekte durchsuchen').fill(property);
    await group.getByRole('option', { name: property, exact: true }).waitFor({ state: 'attached' });
    await field(page, label).selectOption({ label: property });
  }

  let reportScopeId = null;
  async function assertLedger() {
    await page.getByRole('link', { name: 'Mietkonto', exact: true }).click();
    await chooseValue('Mietvertrag *', leaseId);
    if (reportScopeId) await chooseValue('Abrechnungskreis', reportScopeId);
    await field(page, 'Zeitraum von *').fill('2026-01-01');
    await field(page, 'Zeitraum bis *').fill('2026-12-31');
    const response = await responseFor(page, isApi('GET', `/api/payments/mietkonto/lease/${leaseId}`),
      () => button(page, 'Abrufen').click());
    const query = new URL(response.url()).searchParams;
    assert.equal(query.get('period_start'), '2026-01-01');
    assert.equal(query.get('period_end'), '2026-12-31');
    if (reportScopeId) assert.equal(query.get('accounting_scope_id'), reportScopeId);
    const ledger = await successfulJson(response);
    assert.equal(ledger.soll_total, '10200.00');
    assert.equal(ledger.ist_total, '0.00');
    assert.equal(ledger.balance, '10200.00');
    assert.equal(ledger.open_items.length, 24);
    assert.equal(ledger.open_items.filter(item => item.payment_type === 'KAUTION').length, 0);
    const summary = page.getByRole('table').filter({
      has: page.getByRole('cell', { name: 'Soll gesamt', exact: true }),
    });
    for (const [label, expected] of [
      ['Soll gesamt', '10200.00'], ['Ist gesamt', '0.00'], ['Saldo', '10200.00'],
    ]) {
      const row = summary.getByRole('row').filter({ has: page.getByRole('cell', { name: label, exact: true }) });
      await row.waitFor();
      const cells = (await row.getByRole('cell').allTextContents()).map(text => text.trim());
      assert.equal(cells.length, 2);
      assert.equal(cells[0], label);
      const value = label === 'Saldo' ? cells[1].replace(/ \(Rueckstand\)$/, '') : cells[1];
      if (label === 'Saldo') assert.match(cells[1], /\(Rueckstand\)$/);
      assert.equal(money(value), expected);
    }
    const detail = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: 'Offen', exact: true }),
    });
    for (let month = 1; month <= 12; month++) {
      const period = `2026/${String(month).padStart(2, '0')}`;
      for (const [type, label, amount] of [
        ['KALTMIETE', 'Kaltmiete', '700.00'], ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', '150.00'],
      ]) {
        const items = ledger.open_items.filter(item => item.period_year === 2026
          && item.period_month === month && item.payment_type === type);
        assert.equal(items.length, 1, `${period} ${type} exactly once`);
        assert.equal(items[0].soll, amount);
        assert.equal(items[0].ist, '0.00');
        assert.equal(items[0].open_amount, amount);
        const row = detail.getByRole('row')
          .filter({ has: page.getByRole('cell', { name: period, exact: true }) })
          .filter({ has: page.getByRole('cell', { name: label, exact: true }) });
        await moneyCells(row, [period, label], [amount, '0.00', amount]);
      }
    }
    assert.equal(await detail.locator('tbody').getByRole('row').count(), 24);
    assert.equal(await detail.getByRole('cell', { name: /Kaution|KAUTION/ }).count(), 0);
  }
  await assertLedger();

  await page.goto(base + '/berechtigungen');
  await page.getByRole('heading', { name: 'Bereich anlegen', exact: true }).waitFor();
  await selectProperty('Objekt');
  await field(page, 'Bezeichnung').fill(`${prefix} Mietkreis`);
  await field(page, 'Verwaltungsart').selectOption('MIET');
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gesamtes Objekt').check();
  const createdScope = await successfulJson(await responseFor(page,
    isApi('POST', '/api/accounting-scopes'),
    () => button(page, 'Bereich verbindlich anlegen').click()), 201);
  assert.equal(createdScope.property_id, propertyId);
  assert.equal(createdScope.scope_type, 'MIET');
  assert.equal(createdScope.whole_property, true);
  assert.equal(createdScope.valid_from, '2026-01-01');
  await page.getByRole('link', { name: `${prefix} Mietkreis – Teamrechte verwalten`, exact: true }).click();
  await page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }).waitFor();
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await chooseValue('Teammitglied', principal.user_id);
  assert.ok((await field(page, 'Teammitglied').locator('option:checked').textContent()).includes('test@example.de'));
  assert.equal(await field(page, 'Lesen').isChecked(), true);
  await field(page, 'Vorbereiten').check();
  await field(page, 'Bestätigen').check();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(grantForm, 'Begründung').fill('T25 ausdrückliche Freigabe für gebundene Kautionsbuchungen');
  const grant = await successfulJson(await responseFor(page,
    isApi('POST', `/api/accounting-scopes/${createdScope.id}/grants`),
    () => button(grantForm, 'Freigabe erteilen').click()), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['confirm', 'prepare', 'read']);

  await page.goto(base + '/bankkonten');
  await button(page, '+ Neues Bankkonto').click();
  await selectProperty('Objekt *');
  await field(page, 'Kontotyp *').selectOption('KAUTION');
  await field(page, 'Bezeichnung *').fill(account);
  await field(page, 'IBAN').fill('DE02120300000000202051');
  // Explicitly select the property's live MIET scope; no inferred IDs or bypass.
  const scopeSelect = field(page, 'Abrechnungskreis');
  const scopeOption = scopeSelect.getByRole('option', { name: /· MIET ·/ });
  await scopeOption.waitFor({ state: 'attached' });
  assert.equal(await scopeOption.count(), 1, 'Fresh MIET property must offer one unambiguous scope');
  const scopeId = await scopeOption.getAttribute('value');
  assert.ok(scopeId);
  assert.equal(scopeId, createdScope.id);
  await scopeSelect.selectOption(scopeId);
  const bankResponse = await responseFor(page, isApi('POST', '/api/bank-accounts'),
    () => button(page, 'Speichern').click());
  const bank = await successfulJson(bankResponse, 201);
  const accountId = bank.id;
  assert.ok(accountId);
  assert.equal(bank.property_id, propertyId);
  assert.equal(bank.account_type, 'KAUTION');
  assert.equal(bankResponse.request().postDataJSON().accounting_scope_id, scopeId);
  reportScopeId = scopeId;
  assert.equal(bank.label, account);
  await selectProperty('Nach Objekt filtern');
  const bankTable = page.getByRole('table').filter({
    has: page.getByRole('columnheader', { name: 'Kontotyp', exact: true }),
  });
  const bankRow = bankTable.getByRole('row').filter({ has: page.getByRole('cell', { name: account, exact: true }) });
  await bankRow.getByRole('cell', { name: 'Kaution', exact: true }).waitFor();
  assert.equal(await bankRow.getByRole('link', { name: 'Umsätze', exact: true }).getAttribute('href'),
    `/bankkonten/${accountId}/umsaetze`);

  async function overview(paid, refunded, held, reload = false) {
    choosingDepositScope = true;
    if (new URL(page.url()).pathname !== '/kautionen') {
      const link = page.getByRole('link', { name: 'Kautionen', exact: true });
      assert.equal(await link.getAttribute('href'), '/kautionen');
      await link.click();
    }
    await page.getByRole('heading', { name: 'Kautionen', exact: true }).waitFor();
    if (reload) {
      await page.reload();
      await page.getByRole('heading', { name: 'Kautionen', exact: true }).waitFor();
    }
    await chooseValue('Abrechnungskreis', scopeId);
    choosingDepositScope = false;
    const predicate = response => isApi('GET', '/api/payments/deposits')(response)
      && new URL(response.url()).searchParams.get('accounting_scope_id') === scopeId;
    const rows = await successfulJson(await responseFor(page, predicate,
      () => button(page, 'Aktualisieren').click()));
    assert.ok(Array.isArray(rows), 'Deposit endpoint returns a list, not a payment wizard');
    const matching = rows.filter(row => row.lease_agreement_id === leaseId);
    assert.equal(matching.length, 1);
    assert.deepEqual(matching[0], {
      lease_agreement_id: leaseId, tenant_profile_id: tenantId, unit_id: unitId,
      property_id: propertyId, tenant_name: person, unit_label: unit, property_name: property,
      expected_amount: '1800.00', paid_amount: paid, refunded_amount: refunded, held_amount: held,
    });
    const table = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: 'Bestand', exact: true }),
    });
    await table.waitFor();
    assert.deepEqual((await table.getByRole('columnheader').allTextContents()).map(text => text.trim()),
      ['Mieter', 'Objekt', 'Einheit', 'Soll', 'Gezahlt', 'Zurück', 'Bestand']);
    const row = table.getByRole('row').filter({ has: page.getByRole('cell', { name: unit, exact: true }) });
    await moneyCells(row, [`${person} Mietvertrag ab 2026-01-01 (laufend)`, property, unit], ['1800.00', paid, refunded, held]);
    assert.equal(await page.getByRole('link', { name: 'Zahlung erfassen', exact: true }).getAttribute('href'), '/zahlungen');
    assert.equal(await page.getByRole('heading', { name: 'Neue Zahlung anlegen', exact: true }).count(), 0);
  }
  await overview('0.00', '0.00', '0.00');

  const paymentIds = [];
  async function payment(direction, amount, date) {
    await page.getByRole('link', { name: 'Zahlung erfassen', exact: true }).click();
    await page.getByRole('heading', { name: 'Zahlungen', exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/zahlungen');
    await button(page, '+ Neue Zahlung').click();
    await chooseValue('Bankkonto *', accountId);
    await field(page, 'Zahlungstyp *').selectOption('KAUTION');
    await field(page, 'Richtung *').selectOption(direction);
    await field(page, 'Betrag *').fill(amount);
    await field(page, 'Wertstellungsdatum *').fill(date);
    await chooseValue('Mieter', tenantId);
    await chooseValue('Mietvertrag', leaseId);
    const response = await responseFor(page, isApi('POST', '/api/payments'),
      () => button(page, 'Anlegen').click());
    const draft = await successfulJson(response, 201);
    function assertPayment(value, status) {
      assert.equal(value.bank_account_id, accountId);
      assert.equal(value.property_id, propertyId);
      assert.equal(value.tenant_profile_id, tenantId);
      assert.equal(value.lease_agreement_id, leaseId);
      assert.equal(value.payment_type, 'KAUTION');
      assert.equal(value.direction, direction);
      assert.equal(value.amount, amount);
      assert.equal(value.value_date, date);
      assert.equal(value.status, status);
      assert.equal(value.reverses_id, null, 'Refund must not be a reversal');
      assert.equal(value.reversed_by_id, null);
    }
    assertPayment(draft, 'draft');
    assert.ok(draft.id);
    assert.ok(!paymentIds.includes(draft.id));
    paymentIds.push(draft.id);
    await page.waitForURL(base + `/zahlungen/${draft.id}`);
    const details = page.getByRole('table').filter({
      has: page.getByRole('cell', { name: 'Zahlungstyp', exact: true }),
    });
    async function detail(label, text) {
      const row = details.getByRole('row').filter({ has: page.getByRole('cell', { name: label, exact: true }) });
      await exactCells(row, [label, text]);
    }
    await detail('Status', 'Entwurf');
    await detail('Zahlungstyp', 'Kaution');
    await detail('Richtung', direction === 'IN' ? 'Eingang' : 'Ausgang');
    await detail('Betrag', `${amount} EUR`);
    await detail('Wertstellung', date);
    await detail('Bankkonto', `${account} (${property})`);
    await detail('Gegenpartei', `Mieter: ${person}`);
    await detail('Mietvertrag', `${person} ab 2026-01-01 (laufend)`);
    page.once('dialog', async dialog => {
      assert.equal(dialog.type(), 'confirm');
      assert.match(dialog.message(), /^Zahlung buchen\?/);
      await dialog.accept();
    });
    const booked = await successfulJson(await responseFor(page,
      isApi('POST', `/api/payments/${draft.id}/book`), () => button(page, 'Buchen').click()));
    assert.equal(booked.id, draft.id);
    assertPayment(booked, 'booked');
    assert.ok(booked.booked_at);
    await detail('Status', 'Gebucht');
    await detail('Betrag', `${amount} EUR`);
    await detail('Richtung', direction === 'IN' ? 'Eingang' : 'Ausgang');
    assert.equal(await button(page, 'Buchen').count(), 0);
  }
  await payment('IN', '900.00', '2026-01-02');
  await overview('900.00', '0.00', '900.00');
  await payment('IN', '900.00', '2026-01-16');
  await overview('1800.00', '0.00', '1800.00');
  await payment('OUT', '300.00', '2026-02-01');
  await overview('1800.00', '300.00', '1500.00');
  await overview('1800.00', '300.00', '1500.00', true);
  await assertLedger();

  // Observe persisted payments via UI GETs, never via an independent API client.
  for (const id of paymentIds) {
    const persisted = await successfulJson(await responseFor(page, isApi('GET', `/api/payments/${id}`),
      () => page.goto(base + `/zahlungen/${id}`)));
    assert.equal(persisted.status, 'booked');
    assert.equal(persisted.payment_type, 'KAUTION');
    assert.equal(persisted.lease_agreement_id, leaseId);
    assert.equal(persisted.reverses_id, null);
    const statusRow = page.getByRole('table').filter({
      has: page.getByRole('cell', { name: 'Zahlungstyp', exact: true }),
    }).getByRole('row').filter({ has: page.getByRole('cell', { name: 'Status', exact: true }) });
    await exactCells(statusRow, ['Status', 'Gebucht']);
  }
  assert.equal(mutations.filter(request => request.method() === 'POST'
    && new URL(request.url()).pathname === '/api/payments').length, 3);
  assert.equal(mutations.filter(request => request.method() === 'POST'
    && /\/api\/payments\/[^/]+\/book$/.test(new URL(request.url()).pathname)).length, 3);
  assert.equal(mutations.filter(request => ['PATCH', 'PUT', 'DELETE'].includes(request.method())).length, 0,
    'No original lease or existing master data changes');
  assert.equal(mutations.filter(request => /\/reverse$|\/bank-transactions(?:\/|$)|\/rent-changes(?:\/|$)/
    .test(new URL(request.url()).pathname)).length, 0, 'No Storno, bank assignment or rent changes');
  authPhase = 'logging-out';
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
  await Promise.all(scopeProbeReads);
  await context.close();
  const authPermits = new Map();
  for (const url of expectedAuth401) authPermits.set(url, (authPermits.get(url) || 0) + 1);
  for (const url of authConsole401) {
    assert.ok(authPermits.get(url) > 0, `Unexpected session-401 console error: ${url}`);
    authPermits.set(url, authPermits.get(url) - 1);
  }
  const scopePermits = new Map();
  for (const url of expectedScope409) scopePermits.set(url, (scopePermits.get(url) || 0) + 1);
  for (const url of scopeConsole409) {
    assert.ok(scopePermits.get(url) > 0, `Unexpected deposit-409 console error: ${url}`);
    scopePermits.set(url, scopePermits.get(url) - 1);
  }
  console.log(`PASS exact missing-scope probes before explicit selection: ${expectedScope409.length}`);
  assert.deepEqual(errors, [], 'No unexpected HTTP, page or console errors');
  console.log(`PASS expected anonymous/logout session probes: ${expectedAuth401.length}; no authenticated 401 allowed`);
  console.log('PASS T25: UI-only MIET setup and KAUTION account; deposit Soll 1800; two separately drafted/booked IN 900 and genuine OUT 300; persisted overview paid 1800/refunded 300/held 1500; all 24 monthly rent rows unchanged before/after, Soll 10200/Ist 0/Saldo 10200; no runtime errors');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
