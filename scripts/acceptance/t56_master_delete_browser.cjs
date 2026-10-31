#!/usr/bin/env node
/* T56: actual UI — leere Einheit löschen, Einheit mit Vertrag nennt Grund.
 * Parent runs the isolated scripts/dev-local.sh app :13000 / API :18000 only
 * after DB tests are green. This script starts no browser server/DB/fixtures,
 * uses no SQL/API-write client, and produces no artifact files or downloads.
 * Every write is an exact, single-use UI permit. Snapshots use allowlisted GETs.
 * No banks/payments, PDF, SMTP, SEPA, exports, cleanup, or external network.
 * Property audience/grants/immutable creation receipt deliberately remain.
 * Author verification: node --check only; browser closes even on failure.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T56 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} Synthetisches Mietobjekt`, scopeName = `${prefix} Mietkreis`;
const emptyName = `${prefix} Leer`, usedName = `${prefix} Vermietet`;
const firstName = `${prefix} Ada`, lastName = 'Mietlöschtest', personName = `${firstName} ${lastName}`;
const reason = 'T56 synthetischer Quelldatenzugriff; ausdrückliches Lesen, Vorbereiten, Freigeben und Exportieren';
const scopeReason = 'T56 nur Lesen und Vorbereiten im synthetischen MIET-Kreis 2026; kein Versand oder Export';
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).origin === base && new URL(response.url()).pathname === pathname;
const sorted = rows => {
  assert.ok(Array.isArray(rows), 'Canonical inventory must be a complete array');
  assert.equal(new Set(rows.map(row => row.id)).size, rows.length, 'Canonical IDs must be unique');
  return [...rows].sort((a, b) => a.id.localeCompare(b.id));
};
function money(value, cents) {
  assert.equal(typeof value, 'string'); assert.match(value, /^\d+(?:\.\d{1,2})?$/);
  const [whole, fraction = ''] = value.split('.');
  assert.equal(BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0')), BigInt(cents));
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
async function json(response, status = 200) {
  assert.equal(response.status(), status,
    `${typeof response.request === 'function' ? response.request().method() : 'GET'} ${response.url()}: ${response.status()} ${await response.text()}`);
  return response.json();
}
async function selectId(scope, name, id) {
  // SearchChoice/PropertySelector also have text inputs: never use ambiguous getByLabel.
  const select = scope.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${name}: observed option ${id} required`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
}
async function freshReload(page, predicates) {
  // Only requests initiated after the replacement document committed qualify.
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation); page.on('request', started);
  try {
    const waiting = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const results = await Promise.all([...waiting, page.reload()]);
    return Promise.all(results.slice(0, predicates.length).map(response => json(response)));
  } finally { page.off('framenavigated', navigation); page.off('request', started); }
}
let browser;
(async () => {
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
    const context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 1000 } });
    const origins = new Set([base, 'http://127.0.0.1:18000']);
    const sessions = new Set([...origins].map(origin => `${origin}/api/auth/session`));
    const errors = [], consoleErrors = [], writes = [], allowedWrites = [], expectedFailures = [];
    const phases = new WeakMap(), permittedFailures = new WeakMap();
    const writeWhitelist = new Set(['POST /api/auth/login', 'POST /api/properties', 'POST /api/units',
      'POST /api/persons', 'POST /api/person-data-domains', 'POST /api/tenant-profiles',
      'POST /api/accounting-scopes', 'POST /api/leases']);
    let pending = null, authPhase = 'anonymous', page;
    context.on('request', request => phases.set(request, authPhase));
    context.on('page', tab => {
      tab.setDefaultTimeout(20000); tab.setDefaultNavigationTimeout(30000);
      if (page && tab !== page) errors.push('Unexpected extra page/popup');
      tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
      tab.on('dialog', async dialog => { errors.push(`Unexpected native dialog: ${dialog.message()}`); await dialog.dismiss(); });
      tab.on('download', () => errors.push('Unexpected download'));
      tab.on('console', message => {
        if (message.type() !== 'error') return;
        if (/status of 401/.test(message.text()) && sessions.has(message.location().url)
          && authPhase === 'anonymous') return;
        consoleErrors.push({ text: message.text(), url: message.location().url });
      });
    });
    context.on('response', response => {
      if (response.status() < 400) return;
      if (response.status() === 401 && response.request().method() === 'GET'
        && sessions.has(response.url()) && phases.get(response.request()) === 'anonymous') return;
      if (response.status() === 409 && permittedFailures.get(response.request()) === 409) {
        expectedFailures.push({ url: response.url(), status: 409 }); return;
      }
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
        assert.ok(writeWhitelist.has(key), `Not in write whitelist: ${key}`);
        assert.ok(pending && !pending.seen, 'Exactly one pending UI-write permit required');
        assert.equal(key, pending.key); assert.equal(url.origin, base); assert.equal(url.search, '');
        pending.validate(request);
      } catch (error) {
        errors.push(`Unpermitted UI write ${key}: ${error.message}`); return route.abort('blockedbyclient');
      }
      pending.seen = true;
      if (pending.status === 409) permittedFailures.set(request, 409);
      return route.continue();
    });
    assert.equal(typeof context.routeWebSocket, 'function', 'WebSocket blocking support required');
    await context.routeWebSocket('**/*', socket => {
      const url = new URL(socket.url());
      if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
        && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
        socket.connectToServer(); return; // Local Vite HMR only.
      }
      errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
    });
    const clean = () => {
      assert.deepEqual(errors, [], 'No unexpected requests, errors, writes, dialogs or downloads');
      for (const item of consoleErrors) assert.ok(expectedFailures.some(failure => failure.url === item.url
        && new RegExp(`status of ${failure.status}\\b`).test(item.text)),
      `Unexpected console error: ${item.text} ${item.url}`);
    };
    page = await context.newPage();
    const bodyIs = body => request => assert.deepEqual(request.postDataJSON(), body);
    const noBody = request => assert.equal(request.postData(), null, 'DELETE must have no request body');
    async function uiWrite(method, path, action, status, validate) {
      const key = `${method} ${path}`;
      assert.equal(pending, null); assert.ok(writeWhitelist.has(key)); assert.equal(typeof validate, 'function');
      const permit = { key, seen: false, status, validate }; pending = permit; allowedWrites.push(key);
      try {
        const [response] = await Promise.all([page.waitForResponse(isApi(method, path)), action()]);
        assert.equal(permit.seen, true, 'Actual UI must consume exactly one permit');
        if (response.status() !== status) assert.fail(`${key}: ${response.status()} ${await response.text()}`);
        if (status === 204) { assert.equal(response.headers()['content-length'] ?? '0', '0'); clean(); return null; }
        const value = await response.json(); clean(); return value;
      } finally { pending = null; }
    }
    await page.goto(`${base}/objekte`);
    const password = process.env.HV_TEST_PASSWORD || 'testpassword';
    await field(page, 'E-Mail').fill('test@example.de'); await field(page, 'Passwort').fill(password);
    const principal = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200,
      bodyIs({ email: 'test@example.de', password }));
    assert.equal(principal.role, 'OWNER_ADMIN'); assert.ok(principal.user_id); authPhase = 'authenticated';
    await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
    await page.goto(`${base}/objekte`); await button(page, '+ Neues Objekt').click();
    for (const [name, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Löschweg 56'],
      ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, name).fill(value);
    await selectId(page, 'Verwaltungsart *', 'MIET');
    await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
    await field(page, 'Begründung der Freigabe').fill(reason);
    const propertyBody = { construction_year: null, total_area: null, name: propertyName,
      street: 'Synthetischer Löschweg 56', zip_code: '12345', city: 'Teststadt', management_type: 'MIET',
      fiscal_year_start_month: 1, data_audience: { label: propertyName, reason,
        grants: [{ user_id: principal.user_id, capabilities: ['read', 'prepare', 'approve', 'export'], reason }] } };
    const property = await uiWrite('POST', '/api/properties', () => button(page, 'Speichern').click(), 201, request => {
      bodyIs(propertyBody)(request);
      assert.match(request.headers()['idempotency-key'], /^[0-9a-f-]{36}$/i, 'Canonical immutable creation receipt key');
    });
    assert.ok(property.id); assert.equal(property.name, propertyName); assert.equal(property.management_type, 'MIET');
    await searchList(page, 'Objekte suchen'); await page.getByRole('link', { name: propertyName, exact: true }).click();
    const propertyPath = `/objekte/${property.id}`;
    await page.waitForURL(base + propertyPath);
    const units = [];
    for (const number of [emptyName, usedName]) {
      await page.goto(base + propertyPath); await button(page, '+ Neue Einheit').click();
      await field(page, 'Nummer *').fill(number); await field(page, 'Flaeche (m2)').fill('70.00');
      const unit = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click(), 201,
        bodyIs({ property_id: property.id, number, location: '', usage_type: 'WOHNUNG', area: '70.00' }));
      assert.equal(unit.property_id, property.id); assert.equal(unit.number, number); units.push(unit);
      await page.getByRole('link', { name: number, exact: true }).waitFor();
    }
    const [empty, used] = units; assert.notEqual(empty.id, used.id);
    const unitPath = unit => `/objekte/${property.id}/einheiten/${unit.id}`;
    await page.goto(`${base}/personen`); await button(page, '+ Neue Person').click();
    for (const [name, value] of [['Vorname', firstName], ['Nachname *', lastName],
      ['Strasse', 'Synthetischer Kontaktweg 56'], ['PLZ', '12346'], ['Stadt', 'Teststadt']]) await field(page, name).fill(value);
    const person = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click(), 201,
      bodyIs({ first_name: firstName, last_name: lastName, street: 'Synthetischer Kontaktweg 56', zip_code: '12346', city: 'Teststadt' }));
    assert.equal(person.first_name, firstName); assert.equal(person.last_name, lastName);
    await searchList(page, 'Personen suchen'); await page.getByRole('link', { name: personName, exact: true }).click();
    await page.waitForURL(`${base}/personen/${person.id}`);
    await page.goto(`${base}/kontaktdaten-rechte`);
    await field(page, 'Kontakt suchen').fill(firstName); await selectId(page, 'Kontakt', person.id);
    await selectId(page, 'Begründung', 'INITIAL_SETUP');
    const domain = await uiWrite('POST', '/api/person-data-domains', () => button(page, 'Datenbereich vorbereiten').click(), 201,
      bodyIs({ person_id: person.id, reason_code: 'INITIAL_SETUP' }));
    assert.equal(domain.person_id, person.id);
    await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
    await field(page, 'Teammitglied suchen').fill('test@example.de'); await selectId(page, 'Teammitglied', principal.user_id);
    await field(page, 'Bearbeiten').check(); await field(page, 'Exportieren').check();
    await selectId(page, 'Begründung', 'TEAM_ACCESS');
    writeWhitelist.add(`POST /api/person-data-domains/${domain.id}/grants`);
    const contactGrant = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
      () => button(page, 'Teamfreigabe vorbereiten').click(), 201,
      bodyIs({ user_id: principal.user_id, capabilities: ['export', 'prepare', 'read'], reason_code: 'TEAM_ACCESS', expires_at: null }));
    assert.equal(contactGrant.user_id, principal.user_id);
    assert.deepEqual([...contactGrant.capabilities].sort(), ['export', 'prepare', 'read']);
    await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
    await page.goto(`${base}/personen/${person.id}`);
    const tenant = await uiWrite('POST', '/api/tenant-profiles', () => button(page, 'Als Mieter kennzeichnen').click(), 201,
      bodyIs({ person_id: person.id, notes: '' }));
    assert.equal(tenant.person_id, person.id);

    // Explicit full-property financial preparation rights, never authority toggles.
    await page.goto(`${base}/berechtigungen`);
    await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
    await selectId(page, 'Objekt', property.id); await field(page, 'Bezeichnung').fill(scopeName);
    await selectId(page, 'Verwaltungsart', 'MIET'); await field(page, 'Gültig ab').fill('2026-01-01');
    await field(page, 'Gültig bis (optional)').fill(''); await field(page, 'Gesamtes Objekt').check();
    const scope = await uiWrite('POST', '/api/accounting-scopes', () => button(page, 'Bereich verbindlich anlegen').click(), 201,
      bodyIs({ property_id: property.id, management_mandate_id: null, scope_type: 'MIET', label: scopeName,
        valid_from: '2026-01-01', valid_to: null, whole_property: true, units: [] }));
    assert.equal(scope.property_id, property.id); assert.equal(scope.scope_type, 'MIET');
    assert.equal(scope.whole_property, true); assert.equal(scope.valid_from, '2026-01-01'); assert.equal(scope.valid_to, null);
    await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
    const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
    await field(page, 'Teammitglied suchen').fill('test@example.de'); await selectId(page, 'Teammitglied', principal.user_id);
    for (const name of ['Lesen', 'Vorbereiten']) await field(grantForm, name).check();
    for (const name of ['Bestätigen', 'Exportieren', 'Freigeben', 'Versenden']) await field(grantForm, name).uncheck();
    await field(grantForm, 'Begründung').fill(scopeReason);
    writeWhitelist.add(`POST /api/accounting-scopes/${scope.id}/grants`);
    const grant = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
      () => button(grantForm, 'Freigabe erteilen').click(), 201,
      bodyIs({ user_id: principal.user_id, capabilities: ['prepare', 'read'], reason: scopeReason, expires_at: null }));
    assert.equal(grant.user_id, principal.user_id); assert.deepEqual([...grant.capabilities].sort(), ['prepare', 'read']);
    await page.goto(base + unitPath(used)); await button(page, '+ Mietvertrag').click();
    await selectId(page, 'Mieter *', tenant.id);
    for (const [name, value] of [['Vertragsbeginn *', '2026-10-01'], ['Vertragsende', ''],
      ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '150.00']]) await field(page, name).fill(value);
    const lease = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click(), 201,
      bodyIs({ unit_id: used.id, tenant_profile_id: tenant.id, valid_from: '2026-10-01', base_rent: '700.00', advance_payment: '150.00' }));
    assert.equal(lease.unit_id, used.id); assert.equal(lease.tenant_profile_id, tenant.id);
    assert.equal(lease.valid_from, '2026-10-01'); assert.equal(lease.valid_to, null);
    money(lease.base_rent, 70000); money(lease.advance_payment, 15000);

    // Fixed GET-only client: complete business lists and every observed audience/
    // scope + grant page, not just synthetic records. No DTO fields are omitted.
    const lists = ['/api/properties', '/api/units', '/api/persons', '/api/tenant-profiles',
      '/api/owner-profiles', '/api/vendors', '/api/leases', '/api/ownership'];
    const pagedLists = ['/api/property-data-domains/page', '/api/person-data-domains/page', '/api/accounting-scopes/page'];
    const propertyApi = `/api/properties/${property.id}`, emptyApi = `/api/units/${empty.id}`, usedApi = `/api/units/${used.id}`;
    const personApi = `/api/persons/${person.id}`, historyApi = `/api/leases/${lease.id}/rent-changes`;
    const readPaths = new Set([...lists, ...pagedLists, propertyApi, emptyApi, usedApi, personApi, historyApi]);
    async function read(path, query = {}, status = 200) {
      assert.ok(readPaths.has(path), `Unlisted canonical GET: ${path}`);
      assert.ok(Object.keys(query).every(key => ['limit', 'cursor'].includes(key)), 'Only pagination query keys');
      const url = new URL(base + path);
      for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
      const response = await context.request.get(url.href, { maxRedirects: 0 });
      const value = await json(response, status); clean(); return value;
    }
    async function allPages(path) {
      const rows = [], cursors = new Set(); let cursor = null;
      do {
        const result = await read(path, { limit: 200, ...(cursor ? { cursor } : {}) });
        assert.ok(Array.isArray(result.items));
        assert.ok(result.next_cursor === null || (typeof result.next_cursor === 'string' && result.next_cursor.length > 0));
        rows.push(...result.items); cursor = result.next_cursor;
        if (cursor) { assert.equal(cursors.has(cursor), false, 'Pagination must advance'); cursors.add(cursor); }
      } while (cursor);
      return sorted(rows);
    }
    async function snapshot(deleted = false) {
      const value = {};
      for (const path of lists) value[path] = sorted(await read(path));
      for (const path of [propertyApi, usedApi, personApi, historyApi]) value[path] = await read(path);
      if (!deleted) value[emptyApi] = await read(emptyApi);
      for (const path of pagedLists) {
        const rows = await allPages(path); value[path] = rows;
        const root = path.slice(0, -'/page'.length);
        for (const row of rows) {
          const detail = `${root}/${row.id}`, grants = `${detail}/grants/page`;
          readPaths.add(detail); readPaths.add(grants);
          value[detail] = await read(detail); value[grants] = await allPages(grants);
        }
      }
      clean(); return value;
    }
    const before = await snapshot();
    for (const [path, entity] of [[propertyApi, property], [emptyApi, empty], [usedApi, used], [personApi, person]]) {
      assert.deepEqual(before[path], entity, `Creation persisted: ${path}`);
    }
    assert.deepEqual(before['/api/units'].filter(item => item.property_id === property.id), sorted(units));
    assert.deepEqual(before['/api/leases'].filter(item => [empty.id, used.id].includes(item.unit_id)), [lease]);
    assert.deepEqual(before['/api/tenant-profiles'].find(item => item.id === tenant.id), tenant);
    assert.deepEqual(before[historyApi], [], 'Original rent history is empty');
    const audience = before['/api/property-data-domains/page'].filter(item => item.property_id === property.id);
    assert.equal(audience.length, 1, 'Property creation must persist its explicit source audience');
    const audienceGrants = before[`/api/property-data-domains/${audience[0].id}/grants/page`];
    assert.equal(audienceGrants.length, 1); assert.equal(audienceGrants[0].user_id, principal.user_id);
    assert.deepEqual([...audienceGrants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
    assert.equal(audienceGrants[0].revoked_at, null);
    assert.deepEqual(before[`/api/accounting-scopes/${scope.id}`].units, [], 'Whole-property scope has no explicit empty-unit binding');
    assert.ok(before['/api/person-data-domains/page'].some(item => item.id === domain.id));
    assert.ok(before['/api/accounting-scopes/page'].some(item => item.id === scope.id));
    const setupWrites = [...allowedWrites]; assert.equal(setupWrites.length, 11, 'Exactly the explicit synthetic setup writes');
    assert.deepEqual(writes, setupWrites);
    async function unchanged(deleted) {
      const expected = structuredClone(before);
      if (deleted) {
        delete expected[emptyApi];
        expected['/api/units'] = expected['/api/units'].filter(item => item.id !== empty.id);
      }
      assert.deepEqual(await snapshot(deleted), expected,
        'Only empty unit may disappear; every other business DTO, audience, profile, scope and grant stays byte-for-value unchanged');
    }
    const parentUnits = response => isApi('GET', '/api/units')(response)
      && new URL(response.url()).searchParams.get('property_id') === property.id;
    async function verifyParent(deleted) {
      await page.goto(base + propertyPath);
      const [canonical] = await freshReload(page, [parentUnits]);
      assert.deepEqual(sorted(canonical), sorted(deleted ? [used] : units));
      await page.getByRole('link', { name: usedName, exact: true }).waitFor({ state: 'visible' });
      if (deleted) assert.equal(await page.getByRole('link', { name: emptyName, exact: true }).count(), 0);
      else await page.getByRole('link', { name: emptyName, exact: true }).waitFor({ state: 'visible' });
      clean();
    }
    async function visibleOriginalLease() {
      await page.getByRole('heading', { name: `Einheit ${usedName}`, exact: true }).waitFor({ state: 'visible' });
      const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: personName, exact: true }) });
      await row.waitFor({ state: 'visible' }); assert.equal(await row.count(), 1);
      for (const text of ['2026-10-01', 'offen', lease.base_rent, lease.advance_payment]) {
        await row.getByRole('cell', { name: text, exact: true }).waitFor({ state: 'visible' });
      }
      assert.equal(new URL(page.url()).pathname, unitPath(used)); clean();
    }
    const deleteDialog = () => page.getByRole('dialog', { name: 'Einheit löschen', exact: true });
    await page.goto(base + unitPath(empty)); await button(page, 'Einheit löschen').click();
    await deleteDialog().waitFor(); assert.ok((await deleteDialog().innerText()).includes(emptyName));
    await button(deleteDialog(), 'Abbrechen').click(); await deleteDialog().waitFor({ state: 'hidden' });
    assert.deepEqual(writes, setupWrites, 'Opening/cancelling must never send DELETE');
    await verifyParent(false); await unchanged(false);
    assert.deepEqual(writes, setupWrites, 'Cancelled unit remains after actual reload and complete GET snapshot');

    await page.getByRole('link', { name: emptyName, exact: true }).click(); await page.waitForURL(base + unitPath(empty));
    await button(page, 'Einheit löschen').click(); await deleteDialog().waitFor();
    writeWhitelist.add(`DELETE ${emptyApi}`); writeWhitelist.add(`DELETE ${usedApi}`);
    await uiWrite('DELETE', emptyApi, () => button(deleteDialog(), 'Löschen bestätigen').click(), 204, noBody);
    await page.waitForURL(base + propertyPath); await verifyParent(true);
    const missing = await read(emptyApi, {}, 404);
    assert.equal(typeof missing.detail, 'string'); assert.ok(missing.detail.length > 0, 'Deleted unit is canonically 404, not just hidden');
    await unchanged(true);

    await page.getByRole('link', { name: usedName, exact: true }).click(); await page.waitForURL(base + unitPath(used));
    await visibleOriginalLease();
    assert.equal(await button(page, 'Einheit löschen').isEnabled(), true, 'Protection must be the real server 409, not a disabled client button');
    await button(page, 'Einheit löschen').click(); await deleteDialog().waitFor();
    assert.ok((await deleteDialog().innerText()).includes(usedName));
    const conflict = await uiWrite('DELETE', usedApi, () => button(deleteDialog(), 'Löschen bestätigen').click(), 409, noBody);
    assert.equal(typeof conflict.detail, 'string'); assert.match(conflict.detail, /lease|mietvertrag|vertrag/i);
    const exactError = scope => scope.getByRole('alert').filter({ hasText: conflict.detail });
    await exactError(deleteDialog()).waitFor({ state: 'visible' });
    assert.equal(await exactError(deleteDialog()).innerText(), conflict.detail, 'Display exact server conflict detail');
    assert.equal(new URL(page.url()).pathname, unitPath(used), '409 must not navigate away');
    await button(deleteDialog(), 'Abbrechen').click(); await deleteDialog().waitFor({ state: 'hidden' });
    await exactError(page).waitFor({ state: 'visible' }); assert.equal(await exactError(page).innerText(), conflict.detail);
    await visibleOriginalLease(); await unchanged(true);
    const expectedWrites = [...setupWrites, `DELETE ${emptyApi}`, `DELETE ${usedApi}`];
    assert.deepEqual(writes, expectedWrites); assert.equal(expectedFailures.length, 1);

    await page.setViewportSize({ width: 390, height: 844 });
    await exactError(page).waitFor({ state: 'visible' }); assert.equal(await exactError(page).innerText(), conflict.detail);
    await visibleOriginalLease();
    // Reload clears local dialog state, not the protected original unit/contract.
    const [phoneUnit, phoneLeases, phoneHistory] = await freshReload(page, [isApi('GET', usedApi),
      response => isApi('GET', '/api/leases')(response) && new URL(response.url()).searchParams.get('unit_id') === used.id,
      isApi('GET', historyApi)]);
    assert.deepEqual(phoneUnit, before[usedApi]); assert.deepEqual(phoneLeases, [lease]); assert.deepEqual(phoneHistory, []);
    await visibleOriginalLease(); await verifyParent(true);
    await page.getByRole('link', { name: usedName, exact: true }).click(); await page.waitForURL(base + unitPath(used));
    await visibleOriginalLease(); await unchanged(true);
    assert.deepEqual(allowedWrites, expectedWrites); assert.deepEqual(writes, expectedWrites, 'No extra DELETE on reload or phone');
    assert.equal(expectedFailures.length, 1); clean();
    console.log(`T56 PASS: empty unit ${empty.id} cancelled then deleted (204/GET 404); protected unit ${used.id} rejected (409, exact visible detail); 700.00 + 150.00 lease, all other canonical DTOs/grants preserved; 390x844 reload verified`);
  } finally { if (browser) await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
