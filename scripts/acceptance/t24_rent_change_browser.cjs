#!/usr/bin/env node
/* T24: genuine UI rent-change acceptance, following Referenzsoftware RF03/RF05–RF08.
 * Start scripts/dev-local.sh separately against the isolated test DB (55432).
 * UI/API: 127.0.0.1:13000/18000; NEVER target production. All synthetic data
 * and mutations are created through UI controls; API responses only observe UI.
 * No API/SQL seeds, screenshots, report files, bank import or payment creation.
 * Requires existing Playwright + Chrome and test@example.de (OWNER_ADMIN).
 * Run: node scripts/acceptance/t24_rent_change_browser.cjs
 * Synthetic records intentionally remain. No future-only rent-date assumption.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T24 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const property = `${prefix} Mietobjekt`;
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
async function exactCells(row, expected) {
  await row.waitFor({ state: 'visible' });
  assert.equal(await row.count(), 1, 'Exactly one matching row required');
  assert.deepEqual((await row.getByRole('cell').allTextContents()).map(text => text.trim()), expected);
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], mutations = [], expectedAuth401 = [], authConsole401 = [];
  let authPhase = 'anonymous';
  context.on('page', page => {
    page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && url
        && new URL(url).pathname === '/api/auth/session') {
        authConsole401.push(url);
      } else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && authPhase !== 'authenticated'
      && response.request().method() === 'GET'
      && new URL(response.url()).pathname === '/api/auth/session') {
      expectedAuth401.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.url()}`);
  });
  context.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') {
      mutations.push(request);
    }
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  await page.goto(base);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  authPhase = 'authenticated';

  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property);
  const management = page.getByLabel('Verwaltungsart');
  assert.equal(await management.locator('option[value="MIET"]').count(), 1, 'MIET option required for this rent journey');
  await management.selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Mietweg 24');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await field(page, 'Begründung der Freigabe').fill('T24 synthetische UI-Akzeptanz im isolierten lokalen Testsystem');
  await button(page, 'Speichern').click();
  await searchList(page, 'Objekte suchen', prefix);
  await page.getByRole('link', { name: property, exact: true }).click();
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unit);
  await field(page, 'Flaeche (m2)').fill('70');
  await button(page, 'Speichern').click();
  await page.getByRole('link', { name: unit, exact: true }).click();
  const unitUrl = page.url();

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
  const createdLease = await successfulJson(await responseFor(page, response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/leases',
  () => button(page, 'Speichern').click()), 201);
  const leaseId = createdLease.id;
  assert.equal(typeof leaseId, 'string');
  assert.ok(leaseId);
  assert.equal(createdLease.valid_from, '2026-01-01');
  assert.equal(createdLease.base_rent, '700.00');
  assert.equal(createdLease.advance_payment, '150.00');
  const leaseRow = page.getByRole('row').filter({ has: page.getByRole('cell', { name: person, exact: true }) });
  await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
  await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();

  const changesPath = `/api/leases/${leaseId}/rent-changes`;
  const isChanges = method => response => response.request().method() === method
    && new URL(response.url()).pathname === changesPath;
  const changeRows = () => page.getByRole('table', { name: /^Miethistorie:/ }).getByRole('row').filter({
    has: page.getByRole('cell', { name: '2026-04-01', exact: true }),
  });
  async function originalRentVisible() {
    await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
    await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();
    assert.equal(await leaseRow.count(), 1);
    assert.equal(await leaseRow.getByRole('cell', { name: '750.00', exact: true }).count(), 0);
  }
  // Reload ensures the empty history is persisted, not merely a form default.
  const initial = await reloadJson(page, isChanges('GET'));
  assert.deepEqual(initial, []);
  await originalRentVisible();
  await page.getByText(/keine.*Mietänderungen|noch.*Mietänderungen/i).waitFor();
  assert.equal(await changeRows().count(), 0);

  await page.getByRole('button', { name: /^Miete ändern ab/ }).click();
  const dialog = page.getByRole('dialog', { name: 'Miete ändern', exact: true });
  await dialog.waitFor();
  await field(dialog, 'Gültig ab').fill('2026-04-01');
  await field(dialog, 'Kaltmiete (EUR)').fill('750.00');
  await field(dialog, 'Vorauszahlung (EUR)').fill('150.00');
  const changeResponse = await responseFor(page, isChanges('POST'),
    () => button(dialog, 'Mietänderung speichern').click());
  assert.deepEqual(changeResponse.request().postDataJSON(), {
    valid_from: '2026-04-01', base_rent: '750.00', advance_payment: '150.00',
  });
  const change = await successfulJson(changeResponse, 201);
  assert.equal(change.lease_agreement_id, leaseId);
  assert.equal(change.valid_from, '2026-04-01');
  assert.equal(change.base_rent, '750.00');
  assert.equal(change.advance_payment, '150.00');
  await dialog.waitFor({ state: 'hidden' });
  await changeRows().getByRole('cell', { name: '750.00', exact: true }).waitFor();

  const history = await reloadJson(page, isChanges('GET'));
  assert.equal(history.length, 1, 'Exactly one persisted rent change');
  assert.deepEqual(history[0], change);
  await originalRentVisible();
  await exactCells(changeRows(), ['2026-04-01', '750.00', '150.00', 'Mietänderung']);

  // Navigate via the app, and wait for asynchronous native select options attached.
  await page.getByRole('link', { name: 'Mietkonto', exact: true }).click();
  const leaseSelect = field(page, 'Mietvertrag *');
  const option = leaseSelect.locator(`option[value="${leaseId}"]`);
  await option.waitFor({ state: 'attached' });
  assert.ok((await option.textContent()).includes(person));
  await leaseSelect.selectOption(leaseId);
  await field(page, 'Zeitraum von *').fill('2026-01-01');
  await field(page, 'Zeitraum bis *').fill('2026-12-31');
  const ledgerResponse = await responseFor(page, response => response.request().method() === 'GET'
    && new URL(response.url()).pathname === `/api/payments/mietkonto/lease/${leaseId}`,
  () => button(page, 'Abrufen').click());
  const query = new URL(ledgerResponse.url()).searchParams;
  assert.equal(query.get('period_start'), '2026-01-01');
  assert.equal(query.get('period_end'), '2026-12-31');
  const ledger = await successfulJson(ledgerResponse);
  assert.equal(ledger.soll_total, '10650.00', '3 × 850 + 9 × 900');
  assert.equal(ledger.ist_total, '0.00');
  assert.equal(ledger.balance, '10650.00');
  assert.equal(ledger.open_items.length, 24, 'Exactly two rent types for all twelve months');
  const summary = page.getByRole('table').filter({
    has: page.getByRole('cell', { name: 'Soll gesamt', exact: true }),
  });
  for (const [label, value] of [
    ['Soll gesamt', '10650.00 EUR'], ['Ist gesamt', '0.00 EUR'],
    ['Saldo', '10650.00 EUR (Rueckstand)'],
  ]) {
    const row = summary.getByRole('row').filter({ has: page.getByRole('cell', { name: label, exact: true }) });
    await exactCells(row, [label, value]);
  }
  const detail = page.getByRole('table').filter({
    has: page.getByRole('columnheader', { name: 'Offen', exact: true }),
  });
  for (let month = 1; month <= 12; month++) {
    const period = `2026/${String(month).padStart(2, '0')}`;
    for (const [type, label, amount] of [
      ['KALTMIETE', 'Kaltmiete', month <= 3 ? '700.00' : '750.00'],
      ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', '150.00'],
    ]) {
      const items = ledger.open_items.filter(item => item.period_year === 2026
        && item.period_month === month && item.payment_type === type);
      assert.equal(items.length, 1, `${period} ${type} must occur exactly once`);
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
  assert.equal(mutations.filter(request => request.method() === 'POST'
    && new URL(request.url()).pathname === changesPath).length, 1);
  assert.equal(mutations.filter(request => ['PATCH', 'PUT', 'DELETE'].includes(request.method())).length, 0,
    'No original contract or existing master data may be changed');
  assert.equal(mutations.filter(request => /\/api\/(payments|bank-transactions)(?:\/|$)/
    .test(new URL(request.url()).pathname)).length, 0, 'No payments or Soll overrides');
  authPhase = 'logging-out';
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
  await context.close();
  const authPermits = new Map();
  for (const url of expectedAuth401) authPermits.set(url, (authPermits.get(url) || 0) + 1);
  for (const url of authConsole401) {
    assert.ok(authPermits.get(url) > 0, `Unexpected session-401 console error: ${url}`);
    authPermits.set(url, authPermits.get(url) - 1);
  }
  console.log(`PASS expected anonymous/logout session probes: ${expectedAuth401.length}; no authenticated 401 allowed`);
  assert.deepEqual(errors, [], 'No unexpected HTTP, page or console errors');
  console.log('PASS T24: UI-only MIET setup; original lease 700/150 retained; one persisted rent change from 2026-04-01 to 750/150; all 24 browser/API monthly rows verified; Jan–Mar 850, Apr–Dec 900; annual Soll 10650, Ist 0, Saldo 10650; no runtime errors');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
