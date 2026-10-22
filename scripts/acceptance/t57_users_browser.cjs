#!/usr/bin/env node
/* T57 — real mounted UsersPage / login / empty UnitDetail, UI writes only.
 * Parent runs scripts/dev-local.sh :13000/:18000 with isolated fresh /tmp state
 * after all DB fixtures finish. This script starts no server, DB or fixtures,
 * executes no SQL/API writes, sends no mail, downloads nothing, writes no files.
 * Exactly 16 single-use, exact-body UI writes; no cleanup/re-activation write.
 * Canonical GETs are read-only cross-checks, never authentication/setup bypasses.
 * Author verification: node --check ONLY. Parent owns browser run and evidence.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const origins = new Set([base, 'http://127.0.0.1:18000']);
const nonce = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
const prefix = `T57 ${nonce}`, email = `t57-${nonce}@example.invalid`;
const displayName = `${prefix} Lesetest`, propertyName = `${prefix} Synthetisches Mietobjekt`;
const unitName = `${prefix} Leer`, password = `T57-start-${nonce}`, newPassword = `T57-reset-${nonce}`;
const reason = `${prefix} ausdrücklicher Quelldatenzugriff für synthetisches Objekt`;
const grantReason = `${prefix} ausschließlich Lesen der synthetischen Gebäudequelle`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const apiResponse = (method, path) => response => response.request().method() === method
  && new URL(response.url()).origin === base && new URL(response.url()).pathname === path;
const sorted = rows => {
  assert.ok(Array.isArray(rows), 'Complete canonical array required');
  assert.equal(new Set(rows.map(row => row.id)).size, rows.length, 'Unique canonical IDs required');
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
async function checkedJSON(response, expected = 200) {
  // APIResponse GETs have no request() method; diagnostics are deliberately lazy.
  if (response.status() !== expected) assert.fail(`HTTP ${response.status()} ${response.url()}: ${await response.text()}`);
  assert.notEqual(expected, 204, 'Never read a 204 response body');
  return response.json();
}
async function selectId(scope, name, id) {
  const select = scope.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `Observed ${name} option ${id} required`));
  await select.selectOption(id);
}
let browser;
const contexts = [], errors = [], consoles = [], observed401 = [], writes = [], permittedWrites = [];
let pending = null;
const replaceableDomainReads = new Set(), abortedDomainReads = [], completedDomainReads = [];
const noContentAborts = [], acknowledgedNoContent = new WeakSet();
const whitelist = new Set(['POST /api/auth/login', 'POST /api/users', 'POST /api/properties', 'POST /api/units']);
function clean() {
  for (const request of noContentAborts) assert.ok(acknowledgedNoContent.has(request),
    `A bodyless-response abort requires the exact permitted HTTP-204 response: ${request.url()}`);
  const used = new Map();
  for (const key of abortedDomainReads) {
    used.set(key, (used.get(key) || 0) + 1);
    assert.ok(used.get(key) <= completedDomainReads.filter(value => value === key).length,
      `Aborted domain GET requires its own fully completed same-document HTTP-200 replacement: ${key}`);
  }
  assert.deepEqual(errors, [], 'No unexpected request, failure, page error, dialog, download or write');
  const counts = new Map();
  for (const item of consoles) {
    const key = `${item.context}:${item.epoch}:${item.url}`;
    const matching = observed401.filter(failure => failure.context === item.context
      && failure.epoch === item.epoch && failure.url === item.url);
    assert.ok(/Failed to load resource:.*\bstatus of 401\b/.test(item.text), `Unexpected console error: ${item.text} ${item.url}`);
    counts.set(key, (counts.get(key) || 0) + 1);
    assert.ok(counts.get(key) <= matching.length, `Console 401 needs its own expected response: ${key}`);
  }
}
async function guardedContext(name, viewport) {
  const context = await browser.newContext({ serviceWorkers: 'block', viewport }); contexts.push(context);
  const state = { name, context, page: null, epoch: 0, session401: null, inflight: new Set(), navigationAborts: new WeakSet() };
  const expected = new WeakMap(), epochs = new WeakMap(), statuses = new WeakMap();
  context.on('page', page => {
    if (state.page) errors.push(`${name}: unexpected popup`);
    state.page = page; page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(30000);
    page.on('pageerror', error => errors.push(`${name}: pageerror ${error.message}`));
    page.on('download', () => errors.push(`${name}: unexpected download`));
    page.on('dialog', async dialog => { errors.push(`${name}: native dialog ${dialog.message()}`); await dialog.dismiss(); });
    page.on('console', message => {
      if (message.type() === 'error') consoles.push({ context: name, epoch: state.epoch,
        text: message.text(), url: message.location().url });
    });
  });
  context.on('request', request => { state.inflight.add(request); epochs.set(request, state.epoch); });
  context.on('requestfinished', request => {
    state.inflight.delete(request);
    if (request.method() === 'GET' && replaceableDomainReads.has(request.url()) && statuses.get(request) === 200)
      completedDomainReads.push(`${name}:${epochs.get(request)}:${request.url()}`);
  });
  context.on('requestfailed', request => {
    state.inflight.delete(request);
    // Only an individually captured, in-flight GET superseded by our explicit
    // document navigation can be an expected ERR_ABORTED. No blanket abort ignore.
    if (request.failure()?.errorText === 'net::ERR_ABORTED' && request.method() === 'GET'
      && state.navigationAborts.has(request)) return;
    if (request.failure()?.errorText === 'net::ERR_ABORTED' && expected.get(request) === 204) {
      noContentAborts.push(request); return;
    }
    if (request.failure()?.errorText === 'net::ERR_ABORTED' && request.method() === 'GET'
      && replaceableDomainReads.has(request.url())) {
      abortedDomainReads.push(`${name}:${epochs.get(request)}:${request.url()}`); return;
    }
    errors.push(`${name}: requestfailed ${request.method()} ${request.url()} ${request.failure()?.errorText}`);
  });
  context.on('response', response => {
    statuses.set(response.request(), response.status());
    if (response.status() === 204 && expected.get(response.request()) === 204) acknowledgedNoContent.add(response.request());
    if (response.status() < 400) return;
    const request = response.request();
    if (response.status() === 401 && expected.get(request) === 401) {
      observed401.push({ context: name, epoch: epochs.get(request), url: response.url() }); return;
    }
    errors.push(`${name}: HTTP ${response.status()} ${request.method()} ${response.url()}`);
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!origins.has(url.origin) || url.protocol !== 'http:') {
      errors.push(`${name}: forbidden resource ${request.url()}`); return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith('/api/reports/')
      || /\/(?:compute|finalize|pdf|send|export|imports|preview|issue|attest)(?:[/.]|$)/.test(url.pathname)) {
      errors.push(`${name}: forbidden operation ${request.url()}`); return route.abort('blockedbyclient');
    }
    if (request.method() === 'GET') {
      if (url.origin === base && url.pathname === '/api/auth/session' && !url.search
        && state.session401?.epoch === state.epoch && state.session401.remaining > 0) {
        state.session401.remaining--; expected.set(request, 401);
      }
      return route.continue();
    }
    const key = `${request.method()} ${url.pathname}`; writes.push(`${name} ${key}`);
    try {
      assert.ok(whitelist.has(key), `Not whitelisted: ${key}`);
      assert.ok(pending && !pending.seen, 'Exactly one single-use UI permit required');
      assert.equal(pending.state, state); assert.equal(pending.key, key);
      assert.equal(url.origin, base); assert.equal(url.search, '');
      assert.deepEqual(request.postDataJSON(), pending.body);
      if (pending.idempotent) assert.match(request.headers()['idempotency-key'], /^[0-9a-f-]{36}$/i);
      pending.seen = true;
      if (pending.status === 401 || pending.status === 204) expected.set(request, pending.status);
    } catch (error) {
      errors.push(`${name}: unpermitted ${key}: ${error.message}`); return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  assert.equal(typeof context.routeWebSocket, 'function', 'WebSocket guard support required');
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
      && url.searchParams.get('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
      socket.connectToServer(); return; // Exact loopback Vite HMR URL only.
    }
    errors.push(`${name}: forbidden WebSocket ${socket.url()}`); socket.close();
  });
  await context.newPage(); return state;
}
async function navigate(state, path, { reload = false, anonymous = false } = {}) {
  await state.page.waitForLoadState('networkidle');
  const destination = new URL(base + path);
  assert.equal(destination.origin, base);
  // Capture only requests belonging to the actual superseded document.
  for (const request of state.inflight) {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.origin === base && (request.isNavigationRequest()
      || url.pathname.startsWith('/api/'))) state.navigationAborts.add(request);
  }
  state.epoch++;
  state.session401 = anonymous ? { epoch: state.epoch, remaining: 2 } : null;
  if (reload) {
    assert.equal(state.page.url(), destination.href, 'Reload the mounted route, not a substitute URL');
    await state.page.reload();
  } else await state.page.goto(destination.href);
}
async function uiWrite(state, method, path, body, action, status, idempotent = false) {
  const key = `${method} ${path}`;
  assert.equal(pending, null); assert.ok(whitelist.has(key));
  const permit = { state, key, body, status, idempotent, seen: false }; pending = permit;
  permittedWrites.push(`${state.name} ${key}`);
  try {
    const [response] = await Promise.all([state.page.waitForResponse(apiResponse(method, path)), action()]);
    assert.equal(permit.seen, true, 'Actual UI consumed exactly one permit');
    if (response.status() !== status) assert.fail(`${key}: HTTP ${response.status()} ${await response.text()}`);
    // Reset is 204: no text/json/body read, including eager diagnostic messages.
    if (status === 204) return null;
    return response.json();
  } finally { pending = null; }
}
async function login(state, who, secret, status = 200) {
  await field(state.page, 'E-Mail').waitFor({ state: 'visible' });
  await field(state.page, 'E-Mail').fill(who); await field(state.page, 'Passwort').fill(secret);
  const value = await uiWrite(state, 'POST', '/api/auth/login', { email: who, password: secret },
    () => button(state.page, 'Anmelden').click(), status);
  if (status === 200) await state.page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  else {
    assert.equal(typeof value.detail, 'string'); assert.ok(value.detail.length > 0);
    await eventually(async () => assert.equal(await state.page.locator('.login-form__error').innerText(), value.detail));
    assert.equal(await button(state.page, 'Anmelden').isVisible(), true);
    assert.equal(await button(state.page, 'Benutzer anlegen').count(), 0);
  }
  clean(); return value;
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
    const admin = await guardedContext('admin', { width: 1440, height: 1000 }), page = admin.page;
    await navigate(admin, '/users', { anonymous: true });
    const principal = await login(admin, 'test@example.de', 'testpassword');
    assert.equal(principal.role, 'OWNER_ADMIN'); assert.ok(principal.user_id);
    const listPaths = ['/api/users', '/api/properties', '/api/units', '/api/persons', '/api/tenant-profiles',
      '/api/owner-profiles', '/api/vendors', '/api/leases', '/api/ownership'];
    const domainPages = ['/api/property-data-domains/page', '/api/person-data-domains/page', '/api/accounting-scopes/page'];
    const readPaths = new Set([...listPaths, ...domainPages]);
    async function read(path, query = {}) {
      assert.ok(readPaths.has(path), `Unlisted canonical GET ${path}`);
      assert.ok(Object.keys(query).every(key => ['limit', 'cursor'].includes(key)));
      const url = new URL(base + path);
      for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
      const response = await admin.context.request.get(url.href, { maxRedirects: 0 });
      return checkedJSON(response); // Never response.request(): this is APIResponse.
    }
    async function allPages(path) {
      const rows = [], seen = new Set(); let cursor = null;
      do {
        const result = await read(path, { limit: 200, ...(cursor ? { cursor } : {}) });
        assert.ok(Array.isArray(result.items)); rows.push(...result.items);
        cursor = result.next_cursor;
        assert.ok(cursor === null || (typeof cursor === 'string' && cursor.length > 0));
        if (cursor) { assert.equal(seen.has(cursor), false); seen.add(cursor); }
      } while (cursor);
      return sorted(rows);
    }
    async function sources() {
      const result = {};
      for (const path of listPaths.filter(path => path !== '/api/users')) result[path] = sorted(await read(path));
      for (const path of domainPages) {
        const rows = await allPages(path); result[path] = rows;
        for (const row of rows) {
          const root = `${path.slice(0, -5)}/${row.id}`, grants = root + '/grants/page';
          readPaths.add(root); readPaths.add(grants);
          result[root] = await read(root); result[grants] = await allPages(grants);
        }
      }
      for (const path of detailPaths) result[path] = await read(path);
      return result;
    }
    const detailPaths = [];
    const existingUsers = sorted(await read('/api/users')), existingSources = await sources();
    assert.equal(existingUsers.find(user => user.id === principal.user_id)?.role, 'OWNER_ADMIN');
    await navigate(admin, '/users');
    const create = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Neuen Benutzer anlegen', exact: true }) });
    await create.getByPlaceholder('E-Mail *', { exact: true }).fill(email);
    await create.getByPlaceholder('Name', { exact: true }).fill(displayName);
    await create.getByPlaceholder('Startpasswort *', { exact: true }).fill(password);
    await create.getByRole('combobox').selectOption('CLERK');
    const user = await uiWrite(admin, 'POST', '/api/users', { email, password, display_name: displayName, role: 'CLERK' },
      () => button(create, 'Benutzer anlegen').click(), 201);
    assert.ok(user.id); assert.equal(user.role, 'CLERK'); assert.equal(user.is_active, true);
    assert.equal(user.email, email); assert.equal(user.display_name, displayName);
    assert.deepEqual(sorted(await read('/api/users')), sorted([...existingUsers, user]), 'Only synthetic user added');
    await navigate(admin, '/objekte'); await button(page, '+ Neues Objekt').click();
    for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Benutzerweg 57'], ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
    await selectId(page, 'Verwaltungsart *', 'MIET');
    await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
    await field(page, 'Begründung der Freigabe').fill(reason);
    const property = await uiWrite(admin, 'POST', '/api/properties', {
      construction_year: null, total_area: null, name: propertyName, street: 'Synthetischer Benutzerweg 57',
      zip_code: '12345', city: 'Teststadt', management_type: 'MIET', fiscal_year_start_month: 1,
      data_audience: { label: propertyName, reason, grants: [{ user_id: principal.user_id,
        capabilities: ['read', 'prepare', 'approve', 'export'], reason }] },
    }, () => button(page, 'Speichern').click(), 201, true);
    assert.ok(property.id);
    const propertyPath = `/objekte/${property.id}`;
    await navigate(admin, propertyPath); await button(page, '+ Neue Einheit').click();
    await field(page, 'Nummer *').fill(unitName); await field(page, 'Flaeche (m2)').fill('70.00');
    const unit = await uiWrite(admin, 'POST', '/api/units', { property_id: property.id, number: unitName,
      location: '', usage_type: 'WOHNUNG', area: '70.00' }, () => button(page, 'Speichern').click(), 201);
    assert.ok(unit.id); assert.equal(unit.area, '70.00', 'Typed decimal must exactly match canonical GET');
    await page.getByRole('link', { name: unitName, exact: true }).waitFor();
    const domains = (await allPages('/api/property-data-domains/page')).filter(domain => domain.property_id === property.id);
    assert.equal(domains.length, 1, 'Creation persisted explicit property source domain');
    const domain = domains[0], grantsPath = `/api/property-data-domains/${domain.id}/grants/page`;
    readPaths.add(grantsPath);
    replaceableDomainReads.add(`${base}/api/property-data-domains/${domain.id}`);
    await navigate(admin, `/gebaeudedaten-rechte/${domain.id}`);
    await field(page, 'Teammitglied suchen').fill(email); await selectId(page, 'Teammitglied', user.id);
    assert.equal(await field(page, 'Lesen').isChecked(), true);
    for (const capability of ['Bearbeiten', 'Freigeben', 'Exportieren']) assert.equal(await field(page, capability).isChecked(), false);
    await field(page, 'Begründung').fill(grantReason);
    const grantPath = `/api/property-data-domains/${domain.id}/grants`; whitelist.add(`POST ${grantPath}`);
    const grant = await uiWrite(admin, 'POST', grantPath, { user_id: user.id, capabilities: ['read'],
      reason: grantReason, expires_at: null }, () => button(page, 'Teamfreigabe vorbereiten').click(), 201, true);
    assert.equal(grant.user_id, user.id); assert.deepEqual(grant.capabilities, ['read']); assert.equal(grant.revoked_at, null);
    const userPath = `/api/users/${user.id}`, resetPath = userPath + '/reset-password';
    whitelist.add(`PATCH ${userPath}`); whitelist.add(`POST ${resetPath}`); readPaths.add(userPath);
    for (const path of [`/api/properties/${property.id}`, `/api/units/${unit.id}`]) { readPaths.add(path); detailPaths.push(path); }
    const baseline = await sources();
    assert.deepEqual(baseline[detailPaths[0]], property); assert.deepEqual(baseline[detailPaths[1]], unit);
    assert.deepEqual(baseline['/api/units'].filter(row => row.property_id === property.id), [unit]);
    assert.deepEqual(baseline['/api/leases'].filter(row => row.unit_id === unit.id), []);
    assert.deepEqual(baseline['/api/ownership'].filter(row => row.unit_id === unit.id), []);
    const sourceGrants = baseline[grantsPath]; assert.equal(sourceGrants.length, 2);
    assert.deepEqual(sourceGrants.find(row => row.user_id === user.id), { ...grant, user_display_name: displayName });
    const adminGrant = sourceGrants.find(row => row.user_id === principal.user_id);
    assert.ok(adminGrant); assert.equal(adminGrant.revoked_at, null);
    assert.deepEqual([...adminGrant.capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
    // Check the setup touched only its property/unit/domain/grants, not existing sources.
    for (const [path, value] of Object.entries(existingSources)) {
      let actual = baseline[path];
      if (path === '/api/properties') actual = actual.filter(row => row.id !== property.id);
      if (path === '/api/units') actual = actual.filter(row => row.id !== unit.id);
      if (path === '/api/property-data-domains/page') actual = actual.filter(row => row.id !== domain.id);
      assert.deepEqual(actual, value, `Unrelated setup source unchanged: ${path}`);
    }
    async function unchanged(role, active) {
      const expectedUser = { ...user, role, is_active: active };
      assert.deepEqual(await read(userPath), expectedUser, 'Only synthetic role/is_active fields change');
      assert.deepEqual(sorted(await read('/api/users')), sorted([...existingUsers, expectedUser]), 'Every other user unchanged by ID');
      assert.deepEqual(await sources(), baseline, 'All source DTOs, domains and grants unchanged'); clean();
    }
    async function rowAfterReload(state, active, role = 'CLERK') {
      await navigate(state, '/users');
      await navigate(state, '/users', { reload: true });
      await field(state.page, 'Benutzer suchen').fill(email); await button(state.page.getByRole('search'), 'Suchen').click();
      const row = state.page.getByRole('row').filter({ has: state.page.getByRole('cell', { name: email, exact: true }) });
      await row.waitFor();
      await eventually(async () => {
        assert.equal(await row.getByRole('cell', { name: active ? 'Aktiv' : 'Deaktiviert', exact: true }).count(), 1);
        assert.equal(await row.getByRole('cell', { name: role === 'READ_ONLY' ? 'Lesender Benutzer' : 'Sachbearbeiter', exact: true }).count(), 1);
      });
      return row;
    }
    async function openDialog(row, title) {
      await button(row, title).click(); const dialog = page.getByRole('dialog', { name: title, exact: true });
      await dialog.waitFor(); assert.ok((await dialog.innerText()).includes(email)); return dialog;
    }
    let row = await rowAfterReload(admin, true);
    const beforeCancel = writes.length;
    let dialog = await openDialog(row, 'Benutzer bearbeiten');
    await dialog.getByRole('combobox', { name: 'Rolle', exact: true }).selectOption('READ_ONLY'); await field(dialog, 'Aktiv').uncheck();
    await field(dialog, `Deaktivierung bestätigen: ${email} verliert den Zugang.`).check();
    await button(dialog, 'Abbrechen').click(); await dialog.waitFor({ state: 'hidden' });
    dialog = await openDialog(row, 'Passwort zurücksetzen'); await field(dialog, 'Neues Passwort').fill(newPassword);
    await button(dialog, 'Abbrechen').click(); await dialog.waitFor({ state: 'hidden' });
    row = await rowAfterReload(admin, true); assert.equal(writes.length, beforeCancel, 'Cancel/confirmation are write-free');
    await unchanged('CLERK', true);
    dialog = await openDialog(row, 'Benutzer bearbeiten'); await dialog.getByRole('combobox', { name: 'Rolle', exact: true }).selectOption('READ_ONLY');
    const changed = await uiWrite(admin, 'PATCH', userPath, { role: 'READ_ONLY' }, () => button(dialog, 'Änderungen speichern').click(), 200);
    assert.deepEqual(changed, { ...user, role: 'READ_ONLY' }); await dialog.waitFor({ state: 'hidden' });
    row = await rowAfterReload(admin, true, 'READ_ONLY'); await unchanged('READ_ONLY', true);
    const unitPath = `${propertyPath}/einheiten/${unit.id}`;
    async function readerContent(state) {
      const tab = state.page;
      await navigate(state, unitPath);
      // Observe successful, fresh-document session + actual unit GETs on reload.
      let committed = false; const requests = new Set();
      const onFrame = frame => { if (frame === tab.mainFrame()) committed = true; };
      const onRequest = request => { if (committed) requests.add(request); };
      tab.on('framenavigated', onFrame); tab.on('request', onRequest);
      let session, loadedUnit;
      try {
        [session, loadedUnit] = await Promise.all([
          tab.waitForResponse(response => requests.has(response.request()) && apiResponse('GET', '/api/auth/session')(response)).then(checkedJSON),
          tab.waitForResponse(response => requests.has(response.request()) && apiResponse('GET', `/api/units/${unit.id}`)(response)).then(checkedJSON),
          navigate(state, unitPath, { reload: true }),
        ]);
      } finally { tab.off('framenavigated', onFrame); tab.off('request', onRequest); }
      assert.equal(session.user_id, user.id); assert.equal(session.role, 'READ_ONLY'); assert.deepEqual(loadedUnit, unit);
      await tab.getByRole('heading', { name: `Einheit ${unitName}`, exact: true }).waitFor();
      for (const text of ['Keine Eigentumshistorie vorhanden.', 'Keine Mietvertraege vorhanden.']) await tab.getByText(text, { exact: true }).waitFor();
      await tab.getByRole('heading', { name: 'Zähler', exact: true }).waitFor();
      assert.equal(await tab.getByRole('alert').count(), 0, 'Read succeeded, not a denied/error substitute');
      for (const action of ['+ Mietvertrag', '+ Eigentum', 'Einheit löschen', 'Zähler anlegen']) {
        assert.equal(await button(tab, action).count(), 0, `Reader must not see ${action}`);
      }
      assert.equal(await tab.getByRole('button', { name: 'Passwort ändern', exact: true }).count(), 1, 'Own credential maintenance remains available to readers');
      assert.equal(await tab.getByRole('main').getByRole('button', { name: /anlegen|hinzufügen|zuordnen|bearbeiten|löschen|speichern|erfassen|hochladen|archivieren|beenden|ändern|zurücksetzen/i }).count(), 0,
        'Reader has no write controls, including empty-unit new lease, ownership, meters and attachments');
      await navigate(state, propertyPath);
      await tab.getByRole('link', { name: unitName, exact: true }).waitFor();
      assert.equal(await tab.getByRole('button', { name: /Neue Einheit|bearbeiten|löschen/i }).count(), 0);
      await tab.getByRole('link', { name: unitName, exact: true }).click();
      await tab.getByRole('heading', { name: `Einheit ${unitName}`, exact: true }).waitFor(); clean();
    }
    const reader = await guardedContext('reader-desktop', { width: 1440, height: 1000 });
    const phone = await guardedContext('reader-phone', { width: 390, height: 844 });
    for (const state of [reader, phone]) {
      await navigate(state, unitPath, { anonymous: true });
      const session = await login(state, email, password); assert.equal(session.user_id, user.id); assert.equal(session.role, 'READ_ONLY');
      await readerContent(state);
    }
    await unchanged('READ_ONLY', true);
    row = await rowAfterReload(admin, true, 'READ_ONLY'); dialog = await openDialog(row, 'Passwort zurücksetzen');
    assert.equal(await field(dialog, 'Neues Passwort').inputValue(), '', 'Cancelled secret is not retained');
    await field(dialog, 'Neues Passwort').fill(newPassword);
    await uiWrite(admin, 'POST', resetPath, { password: newPassword }, () => button(dialog, 'Passwort zurücksetzen').click(), 204);
    await dialog.waitFor({ state: 'hidden' }); await unchanged('READ_ONLY', true);
    async function cookieRejected(state) {
      const waiting = state.page.waitForResponse(apiResponse('GET', '/api/auth/session'));
      await navigate(state, unitPath, { reload: true, anonymous: true });
      const response = await waiting; assert.equal(response.status(), 401, 'Existing cookie rejected on mounted reload');
      await field(state.page, 'E-Mail').waitFor(); assert.equal(await button(state.page, 'Anmelden').isVisible(), true);
      assert.equal(await state.page.getByRole('heading', { name: `Einheit ${unitName}`, exact: true }).count(), 0); clean();
    }
    for (const state of [reader, phone]) {
      await cookieRejected(state); await login(state, email, password, 401);
      const session = await login(state, email, newPassword); assert.equal(session.user_id, user.id); assert.equal(session.role, 'READ_ONLY');
      await readerContent(state);
    }
    await unchanged('READ_ONLY', true);
    // Mobile admin status and confirmation are exercised without extra setup writes.
    await page.setViewportSize({ width: 390, height: 844 });
    row = await rowAfterReload(admin, true, 'READ_ONLY'); dialog = await openDialog(row, 'Benutzer bearbeiten');
    await field(dialog, 'Aktiv').uncheck(); assert.equal(await button(dialog, 'Änderungen speichern').isEnabled(), false);
    await field(dialog, `Deaktivierung bestätigen: ${email} verliert den Zugang.`).check();
    const deactivated = await uiWrite(admin, 'PATCH', userPath, { is_active: false }, () => button(dialog, 'Änderungen speichern').click(), 200);
    assert.deepEqual(deactivated, { ...user, role: 'READ_ONLY', is_active: false }); await dialog.waitFor({ state: 'hidden' });
    await rowAfterReload(admin, false, 'READ_ONLY'); await unchanged('READ_ONLY', false);
    // FINAL required journey: deactivation revokes both cookies and real login,
    // including a fresh mounted phone login page; no later reset/re-activation.
    for (const state of [reader, phone]) {
      await cookieRejected(state);
      await navigate(state, unitPath, { reload: true, anonymous: true });
      await login(state, email, newPassword, 401);
    }
    await unchanged('READ_ONLY', false);
    assert.equal(permittedWrites.length, 16, 'Exactly bounded setup/security/login writes');
    assert.deepEqual(writes, permittedWrites, 'No unpermitted or duplicate writes, including cancellation'); clean();
    console.log(`T57 PASS: ${user.id}: CLERK→READ_ONLY persisted; explicit READ source grant; desktop/390x844 real read-only reload; password reset 204 invalidated both cookies and old password; new password worked; final deactivation persisted and both cookies/new-password logins rejected (401); other users and all source DTOs unchanged`);
  } finally {
    // Close every context even after a failed assertion or partial context setup.
    const results = await Promise.allSettled(contexts.map(context => context.close()));
    if (browser) await browser.close();
    for (const result of results) if (result.status === 'rejected') errors.push(`Context close failed: ${result.reason}`);
    if (results.some(result => result.status === 'rejected')) assert.fail('One or more browser contexts failed to close');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
