#!/usr/bin/env node
/* T27: real UI only — three leases, one batch click, three uncomputed drafts;
 * identical repetition must skip those same three drafts without duplicates.
 * Root starts the isolated local app separately AFTER backend/UI tests are green.
 * UI 13000/API 18000; never production. No server/DB setup, independent HTTP
 * client, SQL, screenshots, downloads, PDFs or report files in this script.
 * Requires existing Chrome/Playwright and test@example.de/testpassword.
 * Run: node scripts/acceptance/t27_settlement_batch_browser.cjs
 * Synthetic records intentionally remain. Missing accessible labels fail rather
 * than falling back to CSS/index controls or UUIDs as human option labels.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T27 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} Mietobjekt`;
const scopeName = `${prefix} Mietkreis`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
let browser, context;
const hardTimeout = setTimeout(() => {
  console.error('FAIL T27: ten-minute hard timeout');
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
  await field(page, 'Strasse').fill('Synthetischer Sammellaufweg 27');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T27 synthetischer UI-Sammellauf im isolierten lokalen Testsystem');
  const { json: property } = await observe('POST', '/api/properties', () => button(page, 'Speichern').click(), 201);
  assert.equal(property.management_type, 'MIET');
  await search('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  assert.ok(new URL(propertyUrl).pathname.endsWith(`/${property.id}`));

  const units = [], leases = [];
  for (const area of [50, 60, 70]) {
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
  assert.equal(new Set(leases.map(lease => lease.id)).size, 3);

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
  for (const name of ['Lesen', 'Vorbereiten']) await field(form, name).check();
  for (const name of ['Bestätigen', 'Freigeben', 'Exportieren', 'Versenden']) await field(form, name).uncheck();
  await field(form, 'Begründung').fill('T27 ausdrückliche Lese- und Vorbereitungsrechte für unberechnete BK-Entwürfe');
  const { json: grant } = await observe('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(form, 'Freigabe erteilen').click(), 201);
  assert.equal(grant.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['prepare', 'read']);

  await page.goto(base + '/abrechnungen');
  const batchPath = '/api/settlements/batch';
  const batchCount = () => mutations.filter(request => request.method() === 'POST'
    && new URL(request.url()).pathname === batchPath).length;
  const region = page.getByRole('region', { name: 'BK-Sammellauf', exact: true });
  async function openBatch() {
    await button(page, 'BK-Sammellauf').click();
    await region.waitFor();
    await selectLabel(region, 'Objekt *', propertyName);
    await field(region, 'Zeitraum von *').fill('2026-01-01');
    await field(region, 'Zeitraum bis *').fill('2026-12-31');
    await field(region, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(region, 'Abrechnungskreis', `${scopeName} · MIET · 2026-01-01 – offen`);
    assert.equal(await field(region, 'Abrechnungskreis').inputValue(), scope.id);
  }
  // A fully filled but cancelled form is not consent to create drafts.
  await openBatch();
  await button(region, 'Abbrechen').click();
  await region.waitFor({ state: 'hidden' });
  assert.equal(batchCount(), 0);
  const leaseIds = leases.map(lease => lease.id).sort();
  function assertDraft(run) {
    assert.ok(run.id);
    assert.equal(run.property_id, property.id);
    assert.equal(run.period_start, '2026-01-01');
    assert.equal(run.period_end, '2026-12-31');
    assert.equal(run.status, 'draft');
    assert.equal(run.run_type, 'BK_MIETER');
    assert.deepEqual(run.lines, []);
    assert.equal(run.advance_payments_total, null, 'Unknown actual payments must not become zero');
    assert.equal(run.total_cost, null);
    assert.equal(run.balance, null);
    const snapshotKeys = Object.keys(run).filter(key => /snapshot/.test(key));
    assert.ok(snapshotKeys.length, 'SettlementRun must expose its uncomputed snapshot');
    for (const key of snapshotKeys) assert.equal(run[key], null, `${key} must remain uncomputed`);
  }
  async function submit() {
    await openBatch();
    const { response, json } = await observe('POST', batchPath,
      () => button(region, 'Entwürfe anlegen').click());
    assert.deepEqual(response.request().postDataJSON(), {
      property_id: property.id, period_start: '2026-01-01', period_end: '2026-12-31', accounting_scope_id: scope.id,
    }, 'Exact batch request: no money, substitute scope or extra fields');
    assert.deepEqual(Object.keys(json).sort(), ['created', 'skipped']);
    await region.getByRole('status').filter({ hasText: `${json.created.length} Entwürfe angelegt · ${json.skipped.length} bereits vorhanden` }).waitFor();
    return json;
  }
  const first = await submit();
  assert.equal(first.created.length, 3);
  assert.deepEqual(first.skipped, []);
  assert.deepEqual(first.created.map(run => run.lease_agreement_id).sort(), leaseIds);
  first.created.forEach(assertDraft);
  const originalIds = first.created.map(run => run.id).sort();
  assert.equal(new Set(originalIds).size, 3);

  async function filters() {
    await selectLabel(page, 'Objektfilter', propertyName);
    await selectLabel(page, 'Statusfilter', 'Entwurf');
  }
  async function visibleRows() {
    const table = page.getByRole('table');
    const rows = table.getByRole('row').filter({ has: page.getByRole('cell', { name: propertyName, exact: true }) });
    await eventually(async () => {
      assert.equal(await rows.count(), 3, 'Exactly three own-property list rows');
      for (const row of await rows.all()) {
        assert.equal(await row.getByRole('cell', { name: 'Entwurf', exact: true }).count(), 1);
      }
      const hrefs = await rows.getByRole('link').evaluateAll(links => links.map(link => link.getAttribute('href')));
      for (const id of originalIds) assert.ok(hrefs.includes(`/abrechnungen/${id}`), `Missing original draft link ${id}`);
    });
  }
  async function persistedList() {
    await filters();
    await visibleRows();
    const json = await reloadJson(page, isApi('GET', '/api/settlements'));
    const records = Array.isArray(json) ? json : json.items;
    assert.ok(Array.isArray(records), 'Expected settlement list or paginated items');
    const own = records.filter(run => run.property_id === property.id);
    assert.equal(own.length, 3, 'Fresh reload GET retains exactly three own-property drafts');
    assert.deepEqual(own.map(run => run.id).sort(), originalIds);
    assert.deepEqual(own.map(run => run.lease_agreement_id).sort(), leaseIds);
    for (const run of own) {
      assert.equal(run.status, 'draft');
      assert.equal(run.run_type, 'BK_MIETER');
    }
    await filters();
    await visibleRows();
    clean();
  }
  await persistedList();
  const second = await submit();
  assert.deepEqual(second.created, []);
  assert.equal(second.skipped.length, 3);
  assert.deepEqual(second.skipped.map(item => item.lease_agreement_id).sort(), leaseIds);
  assert.deepEqual(second.skipped.map(item => item.settlement_run_id).sort(), originalIds);
  for (const item of second.skipped) {
    assert.deepEqual(Object.keys(item).sort(), ['lease_agreement_id', 'settlement_run_id']);
    assert.equal(item.settlement_run_id,
      first.created.find(run => run.lease_agreement_id === item.lease_agreement_id).id);
  }
  await persistedList();
  assert.equal(batchCount(), 2);
  // Fail closed on every mutation outside the UI fixture and the two batch clicks.
  const allowedPosts = new Set(['/api/auth/login', '/api/properties', '/api/units', '/api/persons',
    '/api/tenant-profiles', '/api/leases', '/api/accounting-scopes',
    `/api/accounting-scopes/${scope.id}/grants`, batchPath]);
  for (const request of mutations) {
    assert.equal(request.method(), 'POST', 'No lease PATCH or existing-record mutation');
    assert.ok(allowedPosts.has(new URL(request.url()).pathname), `Forbidden mutation: ${request.url()}`);
  }
  for (const pathname of ['/api/units', '/api/persons', '/api/tenant-profiles', '/api/leases']) {
    assert.equal(mutations.filter(request => new URL(request.url()).pathname === pathname).length, 3);
  }
  clean();
  authPhase = 'logging-out';
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
  await context.close();
  context = null;
  assert.deepEqual([...session401].sort(), [...console401].sort(), 'Exact 1:1 anonymous/logout session-401 console pairing');
  clean();
  console.log('PASS T27: UI-created three MIET leases; explicit read/prepare scope; one click creates three uncomputed drafts; identical repetition skips the original IDs; fresh reloads retain exactly three; no single create, lease edits, payments, costs, coverage, compute or finalize');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => {
    try { if (context) await context.close(); }
    finally {
      try { if (browser) await browser.close(); }
      finally { clearTimeout(hardTimeout); }
    }
  });
