#!/usr/bin/env node
/* T42: reproducible UI-only acceptance.
 * Start scripts/dev-local.sh separately on UI :13000/API :18000 with the
 * disposable test DB :55432 and isolated temporary document storage.
 * No API/SQL seeds, mocks, fixture/evidence files, cleanup deletes or unrelated
 * mutations. Synthetic records remain. Business writes need single-use permits.
 * Task listitems and the named Fällige Aufgaben dashboard region use actual
 * accessible labels; no CSS-index or raw-UUID label fallbacks.
 * The dashboard window includes today+7 local calendar days. Fresh reloads
 * prove full DTO persistence; financial reads require a conscious READ scope.
 * Existing StrictMode AbortController replays are accepted only for the exact
 * control/new-scope GETs, paired with fully finished replacements on the same
 * page. Chrome's empty logout-body abort requires the exact observed HTTP 204,
 * cookie removal and an actual anonymous session 401 after reload. Other
 * request failures, HTTP errors, console/page errors and mutations remain fatal.
 * node --check proves syntax only; a browser PASS is printed only at the end.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T42 ${Date.now().toString(36)} ${randomUUID().slice(0, 8)}`;
const propertyName = `${prefix} MIET`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} Mietkreis`;
const bankName = `${prefix} Laufendes Konto`;
const noteA = `${prefix} Originaltext <b>keine Formatierung</b> & unverändert`;
const noteB = `${prefix} Einheit Originalnotiz`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const pathname = item => new URL(item.url()).pathname;
const isApi = (method, path) => response => new URL(response.url()).origin === base
  && response.request().method() === method && pathname(response) === path;
// Host-local calendar components, like Python date.today(); no UTC conversion.
const startDay = new Date();
function day(offset) {
  const value = new Date(startDay.getFullYear(), startDay.getMonth(), startDay.getDate() + offset, 12);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}
const today = day(0), tomorrow = day(1), horizon = day(7), future = day(14);
const jan1 = `${startDay.getFullYear()}-01-01`;
async function json(response, status = 200) {
  if (response.status() !== status) assert.fail(`${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  return status === 204 ? null : response.json();
}
async function selectLabel(page, name, label) {
  const select = page.getByRole('combobox', { name, exact: true });
  await select.getByRole('option', { name: label, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label });
}
async function freshReload(page, predicates, afterReload = null) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    const pending = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const reload = (async () => {
      await page.reload();
      if (afterReload) await afterReload();
    })();
    const result = await Promise.all([...pending, reload]);
    return Promise.all(result.slice(0, predicates.length).map(response => json(response)));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
  }
}
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const errors = [], actualWrites = [], allowedWrites = [], writes = [], authResponses = [], authConsole = [];
  const financeRequests = [], scopeRequests = [];
  const controlRequests = new Map(), controlAborts = [], controlFinished = [];
  let logoutReceipt = null;
  const logoutBodyAborts = [];
  // These two existing effects explicitly abort their first dev StrictMode replay.
  const abortableScopeReads = new Set([`${base}/api/accounting-scopes/control`]);
  const isControl = request => request.method() === 'GET' && abortableScopeReads.has(request.url());
  let authPhase = 'anonymous', permit = null, financeBound = false, consciousScopeChoice = false;
  const sessions = new Set([`${base}/api/auth/session`, 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) authConsole.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() === 204 && response.request().method() === 'POST'
      && response.url() === `${base}/api/auth/logout` && authPhase === 'logging-out') logoutReceipt = response.request();
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) authResponses.push(response.url());
    else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  context.on('requestfailed', request => {
    if (request === logoutReceipt && authPhase === 'logging-out' && request.failure()?.errorText === 'net::ERR_ABORTED') {
      logoutBodyAborts.push(request);
    } else if (isControl(request) && request.failure()?.errorText === 'net::ERR_ABORTED' && controlRequests.has(request)) {
      controlAborts.push(controlRequests.get(request));
    } else errors.push(`requestfailed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`);
  });
  context.on('requestfinished', request => {
    if (isControl(request) && controlRequests.has(request)) controlFinished.push(controlRequests.get(request));
  });
  context.on('request', request => {
    if (isControl(request)) controlRequests.set(request, JSON.stringify([request.frame().url(), request.url()]));
    const url = new URL(request.url());
    if (request.method() !== 'GET') return;
    if (url.pathname === '/api/accounting-scopes/page') scopeRequests.push(url);
    if (url.pathname === '/api/fristen') {
      financeRequests.push({ url, financeBound, consciousScopeChoice });
      if (financeBound && (!consciousScopeChoice || !url.searchParams.get('accounting_scope_id')))
        errors.push(`Forbidden financial request before conscious scoped selection: ${url}`);
    }
  });
  // A single-use write permit is consumed BEFORE forwarding the real request.
  // No request is fulfilled/mocked; unexpected mutations are aborted and fatal.
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.method() === 'GET') return route.continue();
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!permit || permit.seen || permit.key !== key || url.origin !== base || url.search) {
      errors.push(`Unexpected write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    permit.seen = true;
    return route.continue();
  });
  const clean = () => assert.deepEqual(errors, [], 'Strict HTTP/console/page/request/write guards');
  const page = await context.newPage();
  page.on('download', () => errors.push('Unexpected download'));
  async function uiWrite(method, path, action, status = 201) {
    assert.equal(permit, null);
    const current = { key: `${method} ${path}`, seen: false };
    permit = current;
    allowedWrites.push(current.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, path)), action()]);
      const data = await json(response, status);
      assert.equal(current.seen, true);
      const payload = response.request().postDataJSON();
      writes.push({ key: current.key, payload });
      clean();
      return { data, payload };
    } finally { permit = null; }
  }
  async function detail(path, uiPath) {
    await page.goto(base + uiPath);
    const [value] = await freshReload(page, [isApi('GET', path)]);
    clean();
    return value;
  }
  await page.goto(base + '/objekte');
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { data: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.ok(principal.user_id);
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  for (const [name, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Aufgabenweg 42'], ['PLZ', '12345'], ['Stadt', 'Teststadt']])
    await field(page, name).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T42 ausdrückliche Quellfreigabe für synthetische Objekt- und Einheitsaufgaben');
  const { data: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties', () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await field(page, 'Objekte suchen').fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyPath = `/objekte/${property.id}`;
  assert.equal(new URL(page.url()).pathname, propertyPath);
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  const { data: unit, payload: unitPayload } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
  assert.equal(unit.property_id, property.id);
  assert.equal(unitPayload.property_id, property.id);
  assert.equal(unitPayload.area, '70');
  assert.match(unit.area, /^70(?:\.0+)?$/);
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitPath = new URL(page.url()).pathname;
  const propertyBefore = await detail(`/api/properties/${property.id}`, propertyPath);
  await page.goto(base + unitPath);
  const [unitBefore, leasesBefore] = await freshReload(page, [isApi('GET', `/api/units/${unit.id}`),
    response => isApi('GET', '/api/leases')(response) && new URL(response.url()).searchParams.get('unit_id') === unit.id]);
  assert.deepEqual(leasesBefore, [], 'Fresh unit has no leases or rent histories to alter');

  function taskBody(title, note, due, unitId = null, propertyId = property.id) {
    return { title, note, due_date: due, assigned_user_id: principal.user_id,
      property_id: propertyId, unit_id: unitId, lease_agreement_id: null };
  }
  function checkTask(value, expected, completed = false, original = null) {
    for (const [key, expectedValue] of Object.entries(expected)) assert.equal(value[key], expectedValue, `Task ${key}`);
    assert.equal(value.completed, completed);
    assert.equal(value.organization_id, principal.organization_id);
    assert.equal(value.created_by, principal.user_id);
    assert.ok(value.id && value.created_at && value.updated_at);
    assert.equal(value.property_name, expected.property_id ? propertyName : null);
    assert.equal(value.unit_number, expected.unit_id ? unitName : null);
    assert.equal(value.lease_label, null);
    assert.ok(value.assigned_user_name && !value.assigned_user_name.includes(principal.user_id), 'Human assignee label, not UUID');
    if (original) {
      const { completed: oldCompleted, updated_at: oldUpdated, ...unchanged } = original;
      const { completed: newCompleted, updated_at: newUpdated, ...actual } = value;
      assert.deepEqual(actual, unchanged, 'Completion may only change completed/updated_at');
      assert.ok(oldCompleted !== undefined && newCompleted !== undefined && oldUpdated && newUpdated);
    }
  }
  async function contextTask(sourcePath, expected, checkEmpty = false) {
    await page.goto(base + sourcePath);
    const before = actualWrites.length;
    await page.getByRole('link', { name: 'Aufgabe anlegen', exact: true }).click();
    await field(page, 'Titel *').waitFor();
    const url = new URL(page.url());
    assert.equal(url.pathname, '/aufgaben');
    assert.equal(url.searchParams.get('create'), '1');
    assert.equal(url.searchParams.get('property_id'), property.id);
    assert.equal(url.searchParams.get('unit_id'), expected.unit_id);
    const form = page.getByRole('dialog');
    assert.equal(await field(form, 'Objekt').inputValue(), property.id);
    assert.equal(await field(form, 'Objekt').getByRole('option', { name: propertyName, exact: true }).count(), 1);
    assert.equal(await field(form, 'Einheit').inputValue(), expected.unit_id || '');
    if (expected.unit_id) assert.equal(await field(form, 'Einheit').getByRole('option', { name: `Einheit ${unitName}`, exact: true }).count(), 1);
    assert.equal(await field(form, 'Mietvertrag').inputValue(), '');
    assert.equal(await field(form, 'Zuständig').inputValue(), principal.user_id);
    assert.equal(actualWrites.length, before, 'Context link must not create a task');
    if (checkEmpty) {
      await button(form, 'Speichern').click();
      assert.equal(await field(form, 'Titel *').evaluate(input => input.validity.valid), false);
      assert.equal(actualWrites.length, before, 'Empty title must not POST');
    }
    await field(form, 'Titel *').fill(expected.title);
    await field(form, 'Notiz').fill(expected.note);
    await field(form, 'Fällig am').fill(expected.due_date);
    const created = await uiWrite('POST', '/api/tasks', () => button(form, 'Speichern').click());
    assert.deepEqual(created.payload, expected, 'Exact task POST, no organization/server-owned/completed fields');
    checkTask(created.data, expected);
    await form.waitFor({ state: 'hidden' });
    return created.data;
  }
  const expectedA = taskBody(`${prefix} Objekt morgen`, noteA, tomorrow);
  const expectedB = taskBody(`${prefix} Einheit später`, noteB, future, unit.id);
  const taskA = await contextTask(propertyPath, expectedA, true);
  const taskB = await contextTask(unitPath, expectedB);
  // Explicit optional-parent/optional-date counterexample, supported by UI test.
  await page.goto(base + '/aufgaben');
  await button(page, 'Aufgabe anlegen').click();
  const globalForm = page.getByRole('dialog');
  const expectedC = taskBody(`${prefix} Global undatiert`, `${prefix} Ohne Finanzkontext`, null, null, null);
  await field(globalForm, 'Titel *').fill(expectedC.title);
  await field(globalForm, 'Notiz').fill(expectedC.note);
  assert.equal(await field(globalForm, 'Objekt').inputValue(), '');
  assert.equal(await field(globalForm, 'Einheit').inputValue(), '');
  assert.equal(await field(globalForm, 'Mietvertrag').inputValue(), '');
  assert.equal(await field(globalForm, 'Fällig am').inputValue(), '');
  const { data: taskC, payload: payloadC } = await uiWrite('POST', '/api/tasks', () => button(globalForm, 'Speichern').click());
  assert.deepEqual(payloadC, expectedC);
  checkTask(taskC, expectedC);
  const taskRows = () => page.getByRole('listitem');
  async function persistedTasks(completed) {
    await page.goto(base + '/aufgaben');
    const [list] = await freshReload(page, [response => isApi('GET', '/api/tasks')(response)
      && !new URL(response.url()).searchParams.has('completed')],
    () => selectLabel(page, 'Status', 'Alle'));
    assert.equal(list.next_offset, null, 'All fixture tasks must be observed, never silently truncate');
    assert.ok(Array.isArray(list.items));
    for (const [original, expected, done] of [[taskA, expectedA, completed], [taskB, expectedB, false], [taskC, expectedC, false]]) {
      const values = list.items.filter(item => item.id === original.id);
      assert.equal(values.length, 1);
      checkTask(values[0], expected, done);
      if (done) checkTask(values[0], expected, true, original);
      else assert.deepEqual(values[0], original, 'Full fresh TaskOut persists unchanged');
      const row = taskRows().filter({ has: page.getByText(expected.title, { exact: true }) });
      await row.waitFor();
      await row.getByText(expected.note, { exact: true }).waitFor();
    }
    clean();
  }
  await persistedTasks(false);

  async function scopeControlReady() {
    await page.getByRole('region', { name: 'Bereichsbezogene Finanzaktionen', exact: true })
      .getByText('Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv', { exact: true }).waitFor();
  }
  // Reproduce the real formerly-409 context, not a mocked financial response.
  await page.goto(base + '/berechtigungen/teamrechte');
  await scopeControlReady();
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName);
  await field(page, 'Bezeichnung').fill(scopeName);
  await selectLabel(page, 'Verwaltungsart', 'MIET');
  await field(page, 'Gültig ab').fill(jan1);
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { data: scope, payload: scopePayload } = await uiWrite('POST', '/api/accounting-scopes', () => button(page, 'Bereich verbindlich anlegen').click());
  for (const value of [scope, scopePayload]) {
    assert.equal(value.property_id, property.id);
    assert.equal(value.scope_type, 'MIET');
    assert.equal(value.whole_property, true);
    assert.equal(value.valid_from, jan1);
    assert.equal(value.valid_to, null);
  }
  abortableScopeReads.add(`${base}/api/accounting-scopes/${scope.id}`);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  await scopeControlReady();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  const member = field(page, 'Teammitglied');
  await member.getByRole('option', { name: /test@example\.de/ }).waitFor({ state: 'attached' });
  await member.selectOption(principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten']) await field(grantForm, label).check();
  for (const label of ['Bestätigen', 'Exportieren', 'Freigeben', 'Versenden']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T42 eigene minimale Kreisrechte Lesen und Vorbereiten zur realen Kontoanlage');
  const { data: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`, () => button(grantForm, 'Freigabe erteilen').click());
  for (const value of [grant, grantPayload]) {
    assert.equal(value.user_id, principal.user_id);
    assert.deepEqual([...value.capabilities].sort(), ['prepare', 'read']);
  }
  async function chooseScope() {
    await field(page, 'Abrechnungskreis suchen').fill(scopeName);
    await selectLabel(page, 'Abrechnungskreis', `${scopeName} · MIET · ${jan1} – offen`);
    assert.equal(await field(page, 'Abrechnungskreis').inputValue(), scope.id);
  }
  await page.goto(base + '/bankkonten');
  await button(page, '+ Neues Bankkonto').click();
  await selectLabel(page, 'Objekt *', propertyName);
  await selectLabel(page, 'Kontotyp *', 'Laufendes Konto');
  await field(page, 'Bezeichnung *').fill(bankName);
  await field(page, 'IBAN').fill('DE02120300000000202051');
  await chooseScope(); // Prepare picker, NOT the dashboard READ selection.
  const { data: bank, payload: bankPayload } = await uiWrite('POST', '/api/bank-accounts', () => button(page, 'Speichern').click());
  financeBound = true;
  for (const value of [bank, bankPayload]) {
    assert.equal(value.property_id, property.id);
    assert.equal(value.account_type, 'LAUFEND');
  }
  assert.equal(bankPayload.accounting_scope_id, scope.id, 'Actual UI POST selects the binding; BankAccountOut does not expose it');
  async function bankSnapshot() {
    await page.goto(base + '/bankkonten');
    const [list] = await freshReload(page, [isApi('GET', '/api/bank-accounts')]);
    assert.ok(Array.isArray(list));
    const matches = list.filter(item => item.id === bank.id);
    assert.equal(matches.length, 1);
    return matches[0];
  }
  const bankBefore = await bankSnapshot();
  const dashboardTasks = response => isApi('GET', '/api/tasks')(response)
    && new URL(response.url()).searchParams.get('completed') === 'false'
    && new URL(response.url()).searchParams.get('due_until') === horizon;
  await page.goto(base + '/');
  const [financialContext, dueTasks] = await freshReload(page, [isApi('GET', '/api/fristen/context'), dashboardTasks]);
  assert.deepEqual(financialContext, { requires_accounting_scope: true });
  assert.equal(await field(page, 'Abrechnungskreis').inputValue(), '', 'No automatic dashboard financial selection');
  assert.equal(dueTasks.items.filter(item => item.id === taskA.id).length, 1);
  assert.equal(dueTasks.items.some(item => [taskB.id, taskC.id].includes(item.id)), false);
  const dashboard = () => page.getByRole('region', { name: 'Fällige Aufgaben', exact: true });
  const dashboardRow = () => dashboard().getByRole('listitem').filter({ has: page.getByText(expectedA.title, { exact: true }) });
  await dashboardRow().waitFor();
  assert.equal(await dashboard().getByText(expectedB.title, { exact: true }).count(), 0);
  assert.equal(await dashboard().getByText(expectedC.title, { exact: true }).count(), 0);
  assert.equal(financeRequests.filter(item => item.financeBound && !item.consciousScopeChoice).length, 0,
    'No financial GET at all before conscious dashboard selection');
  assert.ok(scopeRequests.some(url => url.searchParams.get('capability') === 'read'), 'Dashboard offers a real READ picker');
  consciousScopeChoice = true;
  const [scoped] = await Promise.all([page.waitForResponse(response => isApi('GET', '/api/fristen')(response)
    && new URL(response.url()).searchParams.get('accounting_scope_id') === scope.id), chooseScope()]);
  await json(scoped);
  await dashboardRow().waitFor();
  const { data: done, payload: donePayload } = await uiWrite('PATCH', `/api/tasks/${taskA.id}`, () => button(dashboardRow(), 'Erledigen').click(), 200);
  assert.deepEqual(donePayload, { completed: true });
  checkTask(done, expectedA, true, taskA);
  await dashboardRow().waitFor({ state: 'detached' });
  const [afterDone] = await freshReload(page, [dashboardTasks]);
  assert.equal(afterDone.items.some(item => [taskA.id, taskB.id, taskC.id].includes(item.id)), false);
  assert.equal(await dashboardRow().count(), 0);
  await persistedTasks(true);
  const [completedList] = await freshReload(page, [response => isApi('GET', '/api/tasks')(response)
    && new URL(response.url()).searchParams.get('completed') === 'true'],
    () => selectLabel(page, 'Status', 'Erledigt'));
  assert.ok(completedList.items.some(item => item.id === taskA.id && item.completed));
  await taskRows().filter({ has: page.getByText(expectedA.title, { exact: true }) }).waitFor();

  assert.deepEqual(await detail(`/api/properties/${property.id}`, propertyPath), propertyBefore, 'Property DTO unchanged');
  await page.goto(base + unitPath);
  const [unitAfter, leasesAfter] = await freshReload(page, [isApi('GET', `/api/units/${unit.id}`),
    response => isApi('GET', '/api/leases')(response) && new URL(response.url()).searchParams.get('unit_id') === unit.id]);
  assert.deepEqual(unitAfter, unitBefore, 'Full unit DTO unchanged');
  assert.deepEqual(leasesAfter, leasesBefore, 'No invented lease/history or lease mutation');
  assert.deepEqual(await bankSnapshot(), bankBefore, 'Full bank DTO unchanged');
  const endDay = new Date();
  assert.deepEqual([endDay.getFullYear(), endDay.getMonth(), endDay.getDate()],
    [startDay.getFullYear(), startDay.getMonth(), startDay.getDate()],
    'Run must not cross host-local calendar midnight');
  authPhase = 'logging-out';
  await uiWrite('POST', '/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await field(page, 'E-Mail').waitFor();
  assert.deepEqual(await context.cookies(), [], 'Successful logout removed the session cookie');
  const [anonymous] = await Promise.all([
    page.waitForResponse(response => isApi('GET', '/api/auth/session')(response) && response.status() === 401),
    page.reload(),
  ]);
  assert.equal(anonymous.status(), 401);
  await field(page, 'E-Mail').waitFor();
  assert.ok(logoutReceipt, 'The exact UI logout request received HTTP 204');
  assert.ok(logoutBodyAborts.length <= 1, 'Only the observed empty 204 logout body may be aborted');
  const expectedCounts = new Map([
    ['POST /api/auth/login', 1], ['POST /api/properties', 1], ['POST /api/units', 1],
    ['POST /api/tasks', 3], ['POST /api/accounting-scopes', 1],
    [`POST /api/accounting-scopes/${scope.id}/grants`, 1], ['POST /api/bank-accounts', 1],
    [`PATCH /api/tasks/${taskA.id}`, 1], ['POST /api/auth/logout', 1],
  ]);
  const actualCounts = new Map();
  for (const key of actualWrites) actualCounts.set(key, (actualCounts.get(key) || 0) + 1);
  assert.deepEqual(actualCounts, expectedCounts, 'Complete mutation inventory: no deletes/persons/leases/payments/costs/documents');
  assert.deepEqual(actualWrites, allowedWrites, 'Every UI permit consumed exactly once');
  assert.equal(writes.length, actualWrites.length);
  assert.equal(permit, null);
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Exact anonymous/logout GET session 401/console pairs 1:1');
  for (const key of new Set(controlAborts)) {
    const [navigation, endpoint] = JSON.parse(key);
    assert.ok(new URL(navigation).pathname.startsWith('/berechtigungen'), 'Only the known StrictMode control mount');
    assert.ok(abortableScopeReads.has(endpoint));
    const aborted = controlAborts.filter(value => value === key).length;
    assert.equal(aborted, 1, 'At most one StrictMode replay abort per mounted page');
    assert.ok(controlFinished.filter(value => value === key).length >= aborted,
      'Every control replay abort requires a fully finished real replacement request on the same page');
  }
  clean();
  await context.close();
  await browser.close();
  browser = null;
  console.log(`PASS T42 actual browser run: ${tomorrow} property task completed; context unit/global tasks persisted; minimal real bound account; no unscoped finance GET, strict full mutation/error inventory.`);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
