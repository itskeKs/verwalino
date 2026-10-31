#!/usr/bin/env node
/* T51: actual UI-only synthetic CSV master-data import acceptance.
 * Parent owns prescribed dev-local UI13000/API18000 with isolated temporary data.
 * Starts no server/database; no SQL, fixtures, APIRequestContext or direct API writes.
 * Browser-origin GETs below ONLY verify canonical source DTOs. All commands come
 * from actual clicks/file-input upload. No tenant/owner/vendor profile or lease
 * is implied by a contact. No network destination outside the prescribed app.
 * Requires installed Playwright/Chrome, as T49/T50; no package installation.
 * Selectors follow MasterImport.test.tsx: toolbar "CSV-Stammdaten importieren",
 * "CSV-Vorlage herunterladen", "CSV-Datei", "Vorschau prüfen", CSV-Vorschau,
 * Zeilenfehler, confirmation /geprüften CSV-Zeilen/, "Stammdaten importieren",
 * Importergebnis, audience /Datenbereich anlegen/, "Begründung der Freigabe".
 * Assumes OWNER_ADMIN synthetic login and canonical collection GET permissions.
 * Missing product UI is a failure, never grounds for an API-write fallback.
 * Author has NOT executed this script, browsers, servers, databases or tests.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { TextDecoder } = require('node:util');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const apiOrigin = 'http://127.0.0.1:18000';
const importPath = '/api/properties/import';
const token = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
const prefix = `T51 ${token}`;
const propertyName = `${prefix} Importhaus; Süd`;
const reason = `${prefix}: ausschließlich synthetische lokale Importabnahme`;
const HEADERS = ['kind', 'key', 'property_key', 'name', 'street', 'zip_code', 'city',
  'management_type', 'number', 'area', 'heated_area', 'usage_type',
  'first_name', 'last_name', 'email', 'phone', 'company_name'];
const COUNTS = { properties: 1, units: 4, persons: 4 };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
const unitSpecs = [
  ['60,25', '60.25', '55.00'], ['71.50', '71.50', '66.25'],
  ['82', '82.00', '77.50'], ['93,75', '93.75', '88.75'],
].map(([input, area, heated], index) => ({ key: `U${index + 1}`, number: `${prefix} WE ${index + 1}`, input, area, heated }));
const people = ['Ada', 'Berta', 'Cora', 'Dora'].map((name, index) => ({
  key: `C${index + 1}`, first_name: `${prefix} ${name}`, last_name: 'Synthetisch',
  email: `t51-${token}-${index + 1}@example.invalid`, phone: `+49 00000000510${index + 1}`,
  company_name: index === 3 ? `${prefix} Testfirma` : '',
}));
const records = [
  { kind: 'property', key: 'P1', name: propertyName, street: 'Synthetischer Importweg 51',
    zip_code: '12345', city: 'Teststadt', management_type: 'MIET' },
  ...unitSpecs.map(unit => ({ kind: 'unit', key: unit.key, property_key: 'P1',
    number: unit.number, area: unit.input, heated_area: unit.heated, usage_type: 'WOHNUNG' })),
  ...people.map(person => ({ kind: 'person', ...person })),
];
const identities = records.map((record, index) => ({ line: index + 2, kind: record.kind, key: record.key }));
const quote = value => /[;"\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
const csvText = rows => HEADERS.join(';') + '\r\n'
  + rows.map(row => HEADERS.map(header => quote(row[header] || '')).join(';')).join('\r\n') + '\r\n';
const validCsv = csvText(records);
const invalidCsv = csvText(records.map(record => record.key === 'U1' ? { ...record, area: 'not-an-area' } : record));

// Independent semicolon/quoted-field parser for actual downloaded bytes, not a
// split(',') approximation and not the product parser. Supports CRLF and quotes.
function parseCsv(text) {
  text = text.replace(/^\ufeff/, '');
  const rows = []; let row = [], value = '', quoted = false, closed = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { value += '"'; index++; }
      else if (char === '"') { quoted = false; closed = true; }
      else value += char;
    } else if (char === '"') {
      assert.ok(!value && !closed, 'Quote must begin a field'); quoted = true;
    } else if (char === ';') { row.push(value); value = ''; closed = false; }
    else if (char === '\r' || char === '\n') {
      if (char === '\r' && text[index + 1] === '\n') index++;
      row.push(value); rows.push(row); row = []; value = ''; closed = false;
    } else { assert.equal(closed, false, 'Unexpected text after quote'); value += char; }
  }
  assert.equal(quoted, false, 'Unterminated quoted field');
  if (row.length || value || closed) { row.push(value); rows.push(row); }
  return rows;
}
function decimal(value) {
  assert.equal(typeof value, 'string', 'DTO decimals must remain exact strings');
  assert.match(value, /^\d+(?:\.\d+)?$/);
  const [whole, fraction = ''] = value.split('.');
  const tail = fraction.replace(/0+$/, '');
  return whole.replace(/^0+(?=\d)/, '') + (tail ? `.${tail}` : '');
}
async function json(response, status = 200) {
  assert.equal(response.status(), status, `${response.request().method()} ${response.url()}: ${await response.text()}`);
  return response.json();
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
const ordered = rows => [...rows].sort((left, right) => left.id.localeCompare(right.id));
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: true,
    viewport: { width: 1440, height: 1000 } });
  const errors = [], writes = [], permits = [], importRequests = [], downloads = [];
  let pending = null, authenticated = false, templatePermit = false, templateRequests = 0;
  const sessionUrls = new Set([`${base}/api/auth/session`, `${apiOrigin}/api/auth/session`]);
  const collections = ['properties', 'units', 'persons', 'owner-profiles', 'tenant-profiles', 'vendors', 'leases'];
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('download', download => downloads.push(download));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      if (!authenticated && sessionUrls.has(message.location().url) && /status of 401/.test(message.text())) return;
      errors.push(`console: ${message.text()} ${message.location().url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (!authenticated && response.status() === 401 && response.request().method() === 'GET'
      && sessionUrls.has(response.url())) return;
    errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  // Route every resource, not just APIs: no external image, form, telemetry,
  // email/bank request, service worker or unenumerated mutation may escape.
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (![base, apiOrigin].includes(url.origin) || url.protocol !== 'http:') {
      errors.push(`Forbidden external resource: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith(importPath)) importRequests.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === `${importPath}/template.csv`) {
      if (!templatePermit || request.method() !== 'GET' || url.origin !== base || url.search) {
        errors.push(`Unexpected template request: ${request.url()}`); return route.abort('blockedbyclient');
      }
      templatePermit = false; templateRequests++;
      return route.continue();
    }
    if (request.method() === 'GET') {
      // App-owned UI reads only. No report/export/transport URLs are admitted.
      if (!url.pathname.startsWith('/api/')) return route.continue();
      const allowed = /^\/api\/(?:auth\/session|properties(?:\/page|\/[0-9a-f-]+(?:\/knowledge)?)?|units(?:\/page|\/[0-9a-f-]+)?|persons(?:\/page|\/[0-9a-f-]+)?|owner-profiles|tenant-profiles|vendors|leases|attachments|meters|dsgvo\/persons\/[0-9a-f-]+\/status)(?:$)/i;
      if (allowed.test(url.pathname)) return route.continue();
      errors.push(`Unexpected API GET: ${request.url()}`); return route.abort('blockedbyclient');
    }
    const key = `${request.method()} ${url.pathname}`;
    writes.push(key);
    if (!pending || pending.seen || pending.key !== key || url.origin !== base || url.search) {
      errors.push(`Forbidden UI write: ${key}`); return route.abort('blockedbyclient');
    }
    try { pending.validate(request.postDataJSON()); }
    catch (error) { errors.push(`Rejected command body: ${error.message}`); return route.abort('blockedbyclient'); }
    pending.seen = true;
    return route.continue();
  });
  assert.equal(typeof context.routeWebSocket, 'function', 'Installed Playwright must support WebSocket routing');
  await context.routeWebSocket('**/*', socket => {
    const url = new URL(socket.url());
    if (url.protocol === 'ws:' && url.host === '127.0.0.1:13000' && url.pathname === '/'
      && url.searchParams.has('token') && [...url.searchParams.keys()].every(key => key === 'token')) {
      socket.connectToServer(); return; // Prescribed local Vite HMR only.
    }
    errors.push(`Forbidden WebSocket: ${socket.url()}`); socket.close();
  });
  const page = await context.newPage();
  const clean = () => assert.deepEqual(errors, [], 'No unexpected network/console/page errors');
  async function command(pathname, action, validate, status = 200) {
    assert.equal(pending, null);
    const permit = { key: `POST ${pathname}`, validate, seen: false };
    pending = permit; permits.push(permit.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi('POST', pathname)), action()]);
      assert.equal(permit.seen, true, 'Click must consume exactly one command permit');
      const value = await json(response, status); clean(); return value;
    } finally { pending = null; }
  }
  // The ONLY programmatic fetches: verification GETs to explicit canonical DTO
  // collections. No UI state is injected; no response mocking or API writes.
  async function snapshot() {
    const result = {};
    for (const collection of collections) {
      const response = await page.evaluate(async pathname => {
        const response = await fetch(pathname, { method: 'GET', credentials: 'same-origin', cache: 'no-store' });
        return { status: response.status, body: await response.json() };
      }, `/api/${collection}`);
      assert.equal(response.status, 200, `Verification GET /api/${collection}`);
      assert.ok(Array.isArray(response.body), `${collection}: complete canonical list required`);
      assert.equal(new Set(response.body.map(row => row.id)).size, response.body.length);
      result[collection] = ordered(response.body);
    }
    clean(); return result;
  }
  async function upload(text, filename) {
    // File bytes really include UTF-8 BOM. Product's TextDecoder deliberately
    // strips BOM; the exact expected JSON csv_text is the unchanged rest.
    await field(page, 'CSV-Datei').setInputFiles({ name: filename, mimeType: 'text/csv',
      buffer: Buffer.from(`\ufeff${text}`, 'utf8') });
    await eventually(async () => assert.equal(await button(page, 'Vorschau prüfen').isEnabled(), true));
  }
  async function search(label) {
    await field(page, label).fill(prefix);
    await button(page.getByRole('search'), 'Suchen').click();
  }
  const applyCount = () => writes.filter(key => key === `POST ${importPath}/apply`).length;
  const confirmation = page.getByRole('checkbox', { name: /geprüften CSV-Zeilen/ });
  const applyButton = button(page, 'Stammdaten importieren');
  async function applyBlocked() {
    assert.equal(applyCount(), 0);
    assert.equal(await confirmation.count(), 0, 'Invalid preview must offer no confirmation');
    assert.ok(await applyButton.count() === 0 || await applyButton.isDisabled(), 'Invalid preview cannot apply');
  }
  await page.goto(`${base}/objekte`); // No unrelated dashboard requests.
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  const principal = await command('/api/auth/login', () => button(page, 'Anmelden').click(), body => {
    assert.deepEqual(body, { email: 'test@example.de', password: process.env.HV_TEST_PASSWORD || 'testpassword' });
  });
  assert.equal(principal.role, 'OWNER_ADMIN'); assert.match(principal.user_id, UUID);
  authenticated = true;
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await page.getByRole('heading', { name: 'Objekte', exact: true }).waitFor();
  const opener = button(page, 'CSV-Stammdaten importieren');
  await opener.waitFor();
  assert.equal(await field(page, 'CSV-Datei').count(), 0, 'Toolbar journey must start closed');
  assert.deepEqual(importRequests, [], 'Closed entry must make no import requests');
  const before = await snapshot();
  assert.equal(before.properties.filter(row => row.name === propertyName).length, 0);
  assert.equal(before.persons.filter(row => people.some(person => person.email === row.email)).length, 0);
  await opener.click();
  await field(page, 'CSV-Datei').waitFor();
  assert.deepEqual(importRequests, [], 'Opening must not auto-preview/apply/download');
  const templateLink = page.getByRole('link', { name: 'CSV-Vorlage herunterladen', exact: true });
  assert.equal(await templateLink.getAttribute('href'), `${importPath}/template.csv`);
  assert.notEqual(await templateLink.getAttribute('download'), null);
  templatePermit = true;
  const [download] = await Promise.all([page.waitForEvent('download'), templateLink.click()]);
  assert.equal(new URL(download.url()).pathname, `${importPath}/template.csv`);
  assert.equal(download.suggestedFilename(), 'master-import-template.csv');
  const stream = await download.createReadStream(); assert.ok(stream);
  const chunks = []; let size = 0;
  for await (const chunk of stream) { size += chunk.length; assert.ok(size <= 256 * 1024); chunks.push(Buffer.from(chunk)); }
  assert.equal(await download.failure(), null);
  const bytes = Buffer.concat(chunks);
  assert.ok(bytes.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])), 'Real template has UTF-8 BOM');
  const template = parseCsv(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  assert.deepEqual(template[0], HEADERS);
  assert.equal(template.length, 4, 'Header plus one example for each supported kind');
  for (const row of template) assert.equal(row.length, HEADERS.length);
  assert.deepEqual(template.slice(1).map(row => row[0]), ['property', 'unit', 'person']);
  assert.equal(template[2][HEADERS.indexOf('area')], '60,25');
  assert.equal(download.url(), `${base}${importPath}/template.csv`);
  assert.equal(downloads.length, 1, 'Exactly one actual download event');
  // Chromium native <a download> requests can bypass Playwright routing.
  // Admit only the exact permitted URL, proven by the real artifact above.
  if (templateRequests === 0) {
    assert.equal(templatePermit, true);
    templatePermit = false;
    templateRequests++;
    importRequests.push(`GET ${importPath}/template.csv`);
  }
  assert.equal(templateRequests, 1);

  const audienceBox = page.getByRole('checkbox', { name: /Datenbereich anlegen/ });
  assert.equal(await audienceBox.isChecked(), false, 'No implicit self-grant');
  await upload(invalidCsv, `${token}-invalid.csv`);
  await audienceBox.check();
  assert.equal(await button(page, 'Vorschau prüfen').isDisabled(), true, 'Audience needs explicit reason');
  await field(page, 'Begründung der Freigabe').fill(reason);
  const audience = { label: 'CSV-Stammdatenimport', reason,
    grants: [{ user_id: principal.user_id, capabilities: ['read', 'prepare', 'export'], reason }] };
  const invalidPreview = await command(`${importPath}/preview`, () => button(page, 'Vorschau prüfen').click(), body => {
    assert.deepEqual(body, { csv_text: invalidCsv, data_audience: audience });
  });
  assert.ok(invalidPreview.errors.some(error => error.line === 3 && error.field === 'area' && error.message));
  const rowErrors = page.getByRole('region', { name: 'Zeilenfehler', exact: true });
  await rowErrors.waitFor();
  assert.match(await rowErrors.innerText(), /Zeile 3/); assert.match(await rowErrors.innerText(), /area/);
  await applyBlocked();
  assert.deepEqual(await snapshot(), before, 'Invalid preview must not mutate any canonical source DTOs');

  await upload(validCsv, `${token}-valid.csv`);
  assert.equal(await page.getByRole('table', { name: 'CSV-Vorschau', exact: true }).count(), 0, 'New file invalidates old preview');
  const preview = await command(`${importPath}/preview`, () => button(page, 'Vorschau prüfen').click(), body => {
    assert.deepEqual(body, { csv_text: validCsv, data_audience: audience });
  });
  assert.deepEqual(preview.errors, []); assert.deepEqual(preview.counts, COUNTS);
  assert.match(preview.source_digest, /^[0-9a-f]{64}$/);
  assert.notEqual(preview.source_digest, invalidPreview.source_digest);
  assert.deepEqual(preview.rows.map(({ line, kind, key }) => ({ line, kind, key })), identities);
  const previewTable = page.getByRole('table', { name: 'CSV-Vorschau', exact: true });
  await previewTable.waitFor();
  await page.getByText('1 Objekte · 4 Einheiten · 4 Kontakte', { exact: true }).waitFor();
  for (const unit of unitSpecs) {
    const values = preview.rows.find(row => row.key === unit.key).values;
    assert.equal(decimal(values.area), decimal(unit.area));
    assert.equal(decimal(values.heated_area), decimal(unit.heated));
    await previewTable.getByText(unit.number, { exact: true }).waitFor();
  }
  for (const person of people) await previewTable.getByText(person.email, { exact: true }).waitFor();
  assert.equal(await confirmation.isChecked(), false);
  assert.equal(await applyButton.isDisabled(), true, 'Clean preview alone is not consent');
  assert.deepEqual(await snapshot(), before, 'Valid preview must not write properties, units, contacts, profiles or leases');
  await confirmation.check();
  assert.equal(applyCount(), 0, 'Checking confirmation is still not an apply command');
  const result = await command(`${importPath}/apply`, () => applyButton.click(), body => {
    assert.deepEqual(Object.keys(body).sort(), ['csv_text', 'data_audience', 'expected_digest', 'request_id']);
    assert.deepEqual(body.data_audience, audience); assert.equal(body.csv_text, validCsv);
    assert.equal(body.expected_digest, preview.source_digest); assert.match(body.request_id, UUID);
  }, 201);
  assert.deepEqual(result.counts, COUNTS);
  assert.deepEqual(result.rows.map(({ line, kind, key }) => ({ line, kind, key })), identities);
  for (const row of result.rows) assert.match(row.id, UUID);
  assert.equal(new Set(result.rows.map(row => row.id)).size, 9);
  const ids = new Map(result.rows.map(row => [row.key, row.id]));
  const imported = page.getByRole('region', { name: 'Importergebnis', exact: true });
  await imported.waitFor();
  for (const row of result.rows) {
    const href = row.kind === 'property' ? `/objekte/${row.id}` : row.kind === 'unit'
      ? `/objekte/${ids.get('P1')}/einheiten/${row.id}` : `/personen/${row.id}`;
    const link = imported.getByRole('link', { name: `${row.kind} ${row.key}: ${row.id}`, exact: true });
    assert.equal(await link.getAttribute('href'), href);
  }
  assert.equal(await applyButton.count(), 0, 'Success cannot be submitted again');
  const after = await snapshot();
  for (const [collection, kind, expectedCount] of [['properties', 'property', 1], ['units', 'unit', 4], ['persons', 'person', 4]]) {
    const oldIds = new Set(before[collection].map(row => row.id));
    const additions = after[collection].filter(row => !oldIds.has(row.id));
    assert.equal(after[collection].length, before[collection].length + expectedCount);
    assert.deepEqual(after[collection].filter(row => oldIds.has(row.id)), before[collection], `No existing ${collection} changed`);
    assert.deepEqual(additions.map(row => row.id).sort(), result.rows.filter(row => row.kind === kind).map(row => row.id).sort());
  }
  for (const collection of ['owner-profiles', 'tenant-profiles', 'vendors', 'leases']) {
    assert.deepEqual(after[collection], before[collection], `Import must not imply ${collection}`);
  }
  const property = after.properties.find(row => row.id === ids.get('P1'));
  assert.deepEqual([property.name, property.street, property.zip_code, property.city, property.management_type],
    [propertyName, 'Synthetischer Importweg 51', '12345', 'Teststadt', 'MIET']);
  for (const spec of unitSpecs) {
    const unit = after.units.find(row => row.id === ids.get(spec.key));
    assert.deepEqual([unit.property_id, unit.number, unit.usage_type], [property.id, spec.number, 'WOHNUNG']);
    assert.equal(decimal(unit.area), decimal(spec.area)); assert.equal(decimal(unit.heated_area), decimal(spec.heated));
  }
  for (const spec of people) {
    const person = after.persons.find(row => row.id === ids.get(spec.key));
    assert.deepEqual([person.first_name, person.last_name, person.email, person.phone],
      [spec.first_name, spec.last_name, spec.email, spec.phone]);
    if (spec.company_name) assert.equal(person.company_name, spec.company_name);
  }

  // Navigate via actual result link, then new documents and fresh responses.
  await imported.getByRole('link', { name: `property P1: ${property.id}`, exact: true }).click();
  await page.getByRole('heading', { name: propertyName, exact: true }).waitFor();
  const [propertyResponse, unitsResponse] = await Promise.all([
    page.waitForResponse(isApi('GET', `/api/properties/${property.id}`)),
    page.waitForResponse(response => isApi('GET', '/api/units')(response)
      && new URL(response.url()).searchParams.get('property_id') === property.id),
    page.reload(),
  ]);
  assert.equal((await json(propertyResponse)).name, propertyName);
  assert.equal((await json(unitsResponse)).length, 4);
  await page.getByRole('heading', { name: propertyName, exact: true }).waitFor();
  for (const spec of unitSpecs) {
    const link = page.getByRole('link', { name: spec.number, exact: true }); await link.waitFor();
    const row = link.locator('xpath=ancestor::tr');
    await row.getByRole('cell', { name: spec.area, exact: true }).waitFor();
    await row.getByRole('cell', { name: spec.heated, exact: true }).waitFor();
  }
  for (const person of people) {
    await page.goto(`${base}/personen`); await search('Personen suchen');
    const name = `${person.first_name} ${person.last_name}`;
    await page.getByRole('link', { name, exact: true }).click();
    await page.getByRole('heading', { name, exact: true }).waitFor();
    const [response] = await Promise.all([
      page.waitForResponse(isApi('GET', `/api/persons/${ids.get(person.key)}`)), page.reload(),
    ]);
    assert.equal((await json(response)).email, person.email);
    await page.getByRole('heading', { name, exact: true }).waitFor();
    await page.getByRole('cell', { name: person.email, exact: true }).waitFor();
    await page.getByRole('cell', { name: person.phone, exact: true }).waitFor();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/objekte`); await search('Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).waitFor();
  assert.equal(await field(page, 'CSV-Datei').count(), 0, 'Reloaded import starts closed');
  await opener.click();
  await upload(validCsv, `${token}-phone.csv`); // No second preview or apply.
  assert.equal(await audienceBox.isChecked(), false, 'No persisted/silent audience opt-in on fresh page');
  for (const control of [field(page, 'CSV-Datei'), templateLink, button(page, 'Vorschau prüfen')]) {
    await control.scrollIntoViewIfNeeded(); const box = await control.boundingBox(); assert.ok(box);
    assert.ok(box.x >= 0 && box.x + box.width <= 391, 'Phone import controls must fit 390px viewport');
  }
  assert.equal(await confirmation.count(), 0);
  assert.equal(applyCount(), 1);
  assert.deepEqual(await snapshot(), after, 'Reloads/mobile/file selection must not add writes');
  assert.deepEqual(writes, permits);
  assert.deepEqual(writes, ['POST /api/auth/login', `POST ${importPath}/preview`, `POST ${importPath}/preview`, `POST ${importPath}/apply`]);
  assert.deepEqual(importRequests, [`GET ${importPath}/template.csv`, `POST ${importPath}/preview`, `POST ${importPath}/preview`, `POST ${importPath}/apply`]);
  assert.equal(downloads.length, 1); clean();
  console.log('T51 UI PASS: real BOM/semicolon template download; real file uploads; invalid area blocks apply; explicit admin read/prepare/export audience; both previews leave canonical DTOs unchanged; confirmed single apply creates exactly 1 property/4 units/4 contacts with exact 60.25 and other areas/emails; no implied profiles/leases; source DTO and reloaded UI persistence; 390x844 controls. No external effects.');
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
