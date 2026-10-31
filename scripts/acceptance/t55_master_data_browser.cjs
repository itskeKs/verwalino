#!/usr/bin/env node
/* T55: actual UI-only synthetic property extension and bank-account rename.
 * Parent owns fresh scripts/dev-local.sh UI :13000 / API :18000 and execution.
 * No servers, DB, SQL, fixtures, API mutations via request clients, exports,
 * SMTP, SEPA setup/submission, downloads, external network or artifact files.
 * Only new synthetic data is changed; ending/deleting accounts is not exercised.
 * Author verification: node --check only. Browser always closes in finally.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T55 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} Synthetisches Mietobjekt`;
const originalLabel = `${prefix} T55Original`;
const renamedLabel = `${prefix} T55Mietkonto`;
const reason = 'T55 ausdrücklicher synthetischer Quelldatenzugriff: Lesen, Vorbereiten, Freigeben, Exportieren';
const TEST_IBAN = 'DE89370400440532013000'; // Public synthetic test vector, not a real bank instruction.
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, path) => response => response.request().method() === method
  && new URL(response.url()).origin === base && new URL(response.url()).pathname === path;
const sorted = rows => {
  assert.ok(Array.isArray(rows), 'Canonical inventory must be a complete array');
  assert.equal(new Set(rows.map(row => row.id)).size, rows.length, 'Inventory IDs must be unique');
  return [...rows].sort((a, b) => a.id.localeCompare(b.id));
};
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
async function json(response, status = 200) {
  assert.equal(response.status(), status,
    `${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function selectId(scope, name, id) {
  // PropertySelector wraps a search box and native select: getByLabel is ambiguous.
  const select = scope.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${name}: observed option ${id} required`));
  await select.selectOption(id);
}
async function freshBankReload(page) {
  // Ignore stale responses from requests initiated before the replacement document.
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation); page.on('request', started);
  try {
    const [response] = await Promise.all([
      page.waitForResponse(response => requests.has(response.request()) && isApi('GET', '/api/bank-accounts')(response)),
      page.reload(),
    ]);
    return json(response);
  } finally {
    page.off('framenavigated', navigation); page.off('request', started);
  }
}
function unchangedExceptRename(actual, before) {
  assert.equal(actual.id, before.id);
  assert.equal(actual.property_id, before.property_id);
  assert.equal(actual.account_type, before.account_type);
  assert.equal(actual.iban, before.iban);
  assert.equal(actual.label, renamedLabel);
  const omit = value => Object.fromEntries(Object.entries(value)
    .filter(([key]) => !['label', 'updated_at'].includes(key)));
  assert.deepEqual(omit(actual), omit(before), 'Rename must preserve every other bank DTO field, including active/SEPA state');
  assert.deepEqual(Object.keys(actual).sort(), Object.keys(before).sort(), 'Rename must not drop DTO fields');
}
let browser;
(async () => {
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
    const errors = [], writes = [], allowedWrites = [];
    const origins = new Set([base, 'http://127.0.0.1:18000']);
    const sessions = new Set([...origins].map(origin => `${origin}/api/auth/session`));
    const phases = new WeakMap();
    let authPhase = 'anonymous', pending = null, page;
    context.on('request', request => phases.set(request, authPhase));
    context.on('page', tab => {
      tab.setDefaultTimeout(20000); tab.setDefaultNavigationTimeout(30000);
      if (page && tab !== page) errors.push('Unexpected extra page/popup');
      tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
      tab.on('dialog', async dialog => { errors.push(`Unexpected dialog: ${dialog.message()}`); await dialog.dismiss(); });
      tab.on('download', () => errors.push('Unexpected download'));
      tab.on('console', message => {
        if (message.type() !== 'error') return;
        if (/status of 401/.test(message.text()) && sessions.has(message.location().url) && authPhase === 'anonymous') return;
        errors.push(`console: ${message.text()} ${message.location().url}`);
      });
    });
    context.on('response', response => {
      if (response.status() < 400) return;
      if (response.status() === 401 && response.request().method() === 'GET'
        && sessions.has(response.url()) && phases.get(response.request()) === 'anonymous') return;
      errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
    });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (!origins.has(url.origin) || url.protocol !== 'http:') {
        errors.push(`Forbidden resource: ${request.method()} ${request.url()}`); return route.abort('blockedbyclient');
      }
      if (url.pathname.startsWith('/api/reports/')
        || /\/(?:compute|finalize|pdf|send|export|imports|preview|issue|attest)(?:[/.]|$)/.test(url.pathname)) {
        errors.push(`Forbidden operation: ${request.method()} ${request.url()}`); return route.abort('blockedbyclient');
      }
      if (request.method() === 'GET') return route.continue();
      const key = `${request.method()} ${url.pathname}`; writes.push(key);
      try {
        assert.ok(pending && !pending.seen, 'Single-use UI write permit required');
        assert.equal(key, pending.key); assert.equal(url.origin, base); assert.equal(url.search, '');
        pending.validate(request);
      } catch (error) {
        errors.push(`Unpermitted UI write ${key}: ${error.message}`); return route.abort('blockedbyclient');
      }
      pending.seen = true; return route.continue();
    });
    assert.equal(typeof context.routeWebSocket, 'function', 'WebSocket blocking support required');
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
        && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
        socket.connectToServer(); return;
      }
      errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
    });
    const clean = () => assert.deepEqual(errors, [], 'No unexpected requests, errors, writes, dialogs or downloads');
    page = await context.newPage();
    async function uiWrite(method, path, action, status, validate) {
      assert.equal(pending, null); assert.equal(typeof validate, 'function');
      const permit = { key: `${method} ${path}`, seen: false, validate };
      pending = permit; allowedWrites.push(permit.key);
      try {
        const [response] = await Promise.all([page.waitForResponse(isApi(method, path)), action()]);
        assert.equal(permit.seen, true, 'Actual UI must consume the write permit');
        const value = await json(response, status); clean(); return value;
      } finally { pending = null; }
    }
    const bodyIs = body => request => assert.deepEqual(request.postDataJSON(), body);
    await page.goto(`${base}/objekte`);
    const password = process.env.HV_TEST_PASSWORD || 'testpassword';
    await field(page, 'E-Mail').fill('test@example.de'); await field(page, 'Passwort').fill(password);
    const principal = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200,
      bodyIs({ email: 'test@example.de', password }));
    assert.equal(principal.role, 'OWNER_ADMIN'); assert.ok(principal.user_id); authPhase = 'authenticated';
    await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
    await page.goto(`${base}/objekte`); await button(page, '+ Neues Objekt').click();
    for (const [name, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Stammdatenweg 55'],
      ['PLZ', '12345'], ['Stadt', 'Teststadt'], ['Baujahr', '1984'], ['Gesamtfläche (m²)', '1234.56']]) {
      await field(page, name).fill(value);
    }
    await selectId(page, 'Verwaltungsart *', 'MIET');
    await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
    await field(page, 'Begründung der Freigabe').fill(reason);
    const propertyBody = { name: propertyName, street: 'Synthetischer Stammdatenweg 55', zip_code: '12345',
      city: 'Teststadt', management_type: 'MIET', fiscal_year_start_month: 1,
      construction_year: 1984, total_area: '1234.56', data_audience: { label: propertyName, reason,
        grants: [{ user_id: principal.user_id, capabilities: ['read', 'prepare', 'approve', 'export'], reason }] } };
    const property = await uiWrite('POST', '/api/properties', () => button(page, 'Speichern').click(), 201, bodyIs(propertyBody));
    assert.ok(property.id); assert.equal(property.name, propertyName); assert.equal(property.management_type, 'MIET');
    assert.equal(property.construction_year, 1984); assert.equal(property.total_area, '1234.56');
    await field(page, 'Objekte suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
    await page.getByRole('link', { name: propertyName, exact: true }).waitFor();

    await page.goto(`${base}/bankkonten`); await button(page, '+ Neues Bankkonto').click();
    await field(page.getByRole('group', { name: 'Objekt *', exact: true }), 'Objekte durchsuchen').fill(propertyName);
    await selectId(page, 'Objekt *', property.id); await selectId(page, 'Kontotyp *', 'LAUFEND');
    await field(page, 'Bezeichnung *').fill(originalLabel); await field(page, 'IBAN').fill(TEST_IBAN);
    await field(page, 'BIC').fill('COBADEFFXXX'); await field(page, 'Bank').fill(`${prefix} Synthetische Bank`);
    const bankBody = { property_id: property.id, account_type: 'LAUFEND', label: originalLabel,
      iban: TEST_IBAN, bic: 'COBADEFFXXX', bank_name: `${prefix} Synthetische Bank`, accounting_scope_id: null };
    const bank = await uiWrite('POST', '/api/bank-accounts', () => button(page, 'Speichern').click(), 201, bodyIs(bankBody));
    assert.ok(bank.id); assert.equal(bank.property_id, property.id); assert.equal(bank.account_type, 'LAUFEND');
    assert.equal(bank.label, originalLabel); assert.equal(bank.iban, TEST_IBAN);
    if (Object.hasOwn(bank, 'is_active')) assert.equal(bank.is_active, true);
    const rowFor = label => page.getByRole('row').filter({ has: page.getByRole('cell', { name: label, exact: true }) });
    await rowFor(originalLabel).waitFor();

    // Read-only client, fixed allowlist. No request-client mutation is permitted.
    const lists = ['/api/properties', '/api/bank-accounts', '/api/units', '/api/persons', '/api/vendors',
      '/api/owner-profiles', '/api/tenant-profiles', '/api/leases', '/api/ownership', '/api/payments',
      '/api/economic-plans', '/api/cost-entries', '/api/invoice-documents', '/api/cost-categories'];
    const propertyPath = `/api/properties/${property.id}`, bankPath = `/api/bank-accounts/${bank.id}`;
    const readPaths = new Set([...lists, propertyPath, bankPath]);
    async function read(path) {
      assert.ok(readPaths.has(path), `Unlisted snapshot GET: ${path}`);
      const response = await context.request.get(base + path, { maxRedirects: 0 });
      assert.equal(response.status(), 200, `GET ${path}: ${response.status()} ${await response.text()}`);
      clean(); return response.json();
    }
    async function snapshot() {
      const value = {};
      for (const path of lists) value[path] = sorted(await read(path));
      value[propertyPath] = await read(propertyPath); value[bankPath] = await read(bankPath);
      clean(); return value;
    }
    const before = await snapshot();
    assert.deepEqual(before[propertyPath], property, 'Created property persists canonically');
    assert.deepEqual(before[bankPath], bank, 'Created bank persists canonically');
    assert.equal(before['/api/bank-accounts'].filter(item => item.id === bank.id).length, 1);
    async function verifySnapshot() {
      const after = await snapshot();
      unchangedExceptRename(after[bankPath], before[bankPath]);
      for (const path of lists) {
        if (path !== '/api/bank-accounts') assert.deepEqual(after[path], before[path], `Source DTOs unchanged: ${path}`);
        else {
          assert.deepEqual(after[path].map(item => item.id), before[path].map(item => item.id));
          for (let i = 0; i < before[path].length; i++) {
            if (before[path][i].id === bank.id) unchangedExceptRename(after[path][i], before[path][i]);
            else assert.deepEqual(after[path][i], before[path][i], 'Existing bank accounts must remain untouched');
          }
        }
      }
      assert.deepEqual(after[propertyPath], before[propertyPath], 'Property source remains unchanged');
    }
    await button(rowFor(originalLabel), 'Bankkonto bearbeiten').click();
    const editForm = page.getByRole('dialog', { name: 'Bankkonto bearbeiten', exact: true }).locator('form');
    await page.getByRole('heading', { name: 'Bankkonto bearbeiten', exact: true }).waitFor();
    assert.equal(await field(editForm, 'Bezeichnung').inputValue(), originalLabel);
    await field(editForm, 'Bezeichnung').fill(renamedLabel);
    const renamed = await uiWrite('PATCH', bankPath, () => button(editForm, 'Änderungen speichern').click(), 200, request => {
      const body = request.postDataJSON();
      assert.equal(body.label, renamedLabel);
      const allowed = new Set(['label', 'bank_name', 'iban', 'bic', 'is_active']);
      for (const [key, value] of Object.entries(body)) {
        assert.ok(allowed.has(key), `Forbidden bank PATCH field ${key}`);
        if (key !== 'label') assert.deepEqual(value, before[bankPath][key], `Rename must not mutate ${key}`);
      }
    });
    unchangedExceptRename(renamed, before[bankPath]);
    await rowFor(renamedLabel).waitFor(); assert.equal(await rowFor(originalLabel).count(), 0);
    await verifySnapshot();
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      const reloaded = sorted(await freshBankReload(page));
      const persisted = reloaded.filter(item => item.id === bank.id); assert.equal(persisted.length, 1);
      unchangedExceptRename(persisted[0], before['/api/bank-accounts'].find(item => item.id === bank.id));
      const row = rowFor(renamedLabel); await row.waitFor(); assert.equal(await row.count(), 1);
      assert.equal(await rowFor(originalLabel).count(), 0);
      for (const text of [propertyName, 'Laufendes Konto', TEST_IBAN, bankBody.bank_name]) {
        await row.getByRole('cell', { name: text, exact: true }).waitFor({ state: 'visible' });
      }
      await button(row, 'Bankkonto bearbeiten').click();
      await page.getByRole('heading', { name: 'Bankkonto bearbeiten', exact: true }).waitFor();
      assert.equal(await field(editForm, 'Bezeichnung').inputValue(), renamedLabel);
      await button(editForm, 'Abbrechen').click();
      await verifySnapshot(); clean();
    }
    assert.deepEqual(allowedWrites, ['POST /api/auth/login', 'POST /api/properties',
      'POST /api/bank-accounts', `PATCH ${bankPath}`]);
    assert.deepEqual(writes, allowedWrites, 'Exactly the permitted UI writes, no retries or side effects');
    clean();
    console.log(`T55 PASS: ${bank.id} renamed through UI; property 1984 / 1234.56, canonical sources preserved, desktop and 390x844 reload verified`);
  } finally {
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
