#!/usr/bin/env node
/* T30: actual local UI only; run scripts/dev-local.sh against test DB 55432.
 * No API/SQL seeds, generated evidence files or production access.
 * Fixed readings: 100.0000 -> 250.1250 = 150.1250; 0 -> 12.5000.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const tag = `T30 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const propertyName = `${tag} Haus`;
const unitName = `${tag} Wohnung`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const path = response => new URL(response.url()).pathname;
let browser;

async function uiResponse(page, method, pathname, action) {
  const [response] = await Promise.all([
    page.waitForResponse(r => r.request().method() === method && path(r) === pathname), action(),
  ]);
  assert.equal(response.status(), method === 'POST' ? 201 : 200,
    `${method} ${pathname}: ${response.status()} ${await response.text()}`);
  return { body: response.request().postDataJSON(), data: await response.json() };
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const errors = [], mutations = [], authResponses = [], authConsole = [], histories = [];
  let phase = 'anonymous', generation = 0;
  const requests = new WeakMap();
  function authUrl(value) {
    const url = new URL(value);
    return [base, 'http://127.0.0.1:18000'].includes(url.origin) && url.pathname === '/api/auth/session';
  }
  context.on('page', page => {
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) generation++; });
    page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && url && authUrl(url)) authConsole.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('request', request => {
    requests.set(request, generation);
    if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') mutations.push(request);
  });
  context.on('response', response => {
    if (response.status() >= 400) {
      if (response.status() === 401 && phase !== 'authenticated'
        && response.request().method() === 'GET' && authUrl(response.url())) authResponses.push(response.url());
      else errors.push(`HTTP ${response.status()}: ${response.url()}`);
    }
    if (response.status() === 200 && response.request().method() === 'GET'
      && /^\/api\/meters\/[^/]+\/readings$/.test(path(response))) {
      const json = response.json().catch(error => {
        errors.push(`History response could not be read: ${error.message}`);
        return null;
      });
      histories.push({ pathname: path(response), generation: requests.get(response.request()), json });
    }
  });
  const page = await context.newPage();
  await page.goto(base + '/objekte');
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  phase = 'authenticated';
  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(propertyName);
  await field(page, 'Verwaltungsart *').selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Zählerweg 30');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await field(page, 'Begründung der Freigabe').fill('T30 synthetische Zählerprüfung im isolierten lokalen Testsystem');
  const createdProperty = await uiResponse(page, 'POST', '/api/properties', () => button(page, 'Speichern').click());
  const propertyId = createdProperty.data.id;
  await field(page, 'Objekte suchen').fill(tag);
  await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  const panel = () => page.getByRole('region', { name: 'Zähler', exact: true });
  await panel().waitFor();

  async function createMeter(number, unitId) {
    await button(panel(), 'Zähler anlegen').click();
    await field(panel(), 'Zählernummer *').fill(number);
    await field(panel(), 'Zählertyp *').fill('KALTWASSER');
    await field(panel(), 'Standort').fill(unitId ? 'Wohnung' : 'Technikraum');
    const created = await uiResponse(page, 'POST', '/api/meters', () => button(panel(), 'Zähler speichern').click());
    assert.equal(created.body.property_id, propertyId);
    assert.equal(created.body.unit_id ?? null, unitId ?? null);
    assert.equal(created.body.meter_number, number);
    assert.equal(created.body.meter_type, 'KALTWASSER');
    assert.equal(created.data.property_id, propertyId);
    assert.equal(created.data.unit_id, unitId ?? null);
    assert.equal(created.data.meter_number, number);
    return created.data;
  }
  async function openHistory(meter) {
    const row = panel().getByRole('row').filter({ has: page.getByRole('cell', { name: meter.meter_number, exact: true }) });
    await row.waitFor({ state: 'visible' });
    assert.equal(await row.count(), 1);
    await button(row, 'Verlauf anzeigen').click();
    await button(panel(), 'Stand erfassen').waitFor();
  }
  async function addReading(meter, day, input, exact) {
    await button(panel(), 'Stand erfassen').click();
    await field(panel(), 'Ablesedatum *').fill(day);
    await field(panel(), 'Zählerstand *').fill(input);
    await field(panel(), 'Zeitraum').fill('2026');
    const created = await uiResponse(page, 'POST', `/api/meters/${meter.id}/readings`, () => button(panel(), 'Stand speichern').click());
    assert.deepEqual(created.body, { reading_date: day, value: exact, period: '2026' });
    assert.equal(created.data.meter_id, meter.id);
    assert.equal(created.data.reading_date, day);
    assert.equal(created.data.value, exact);
    await panel().getByRole('cell', { name: exact.replace('.', ','), exact: true }).waitFor();
    return created.data;
  }
  async function persisted(meter, expected, difference) {
    await page.reload();
    await panel().waitFor();
    await openHistory(meter);
    await panel().getByRole('status', { name: 'Verbrauch', exact: true })
      .filter({ hasText: `Verbrauch: ${difference}` }).waitFor();
    const observed = histories.filter(row => row.pathname === `/api/meters/${meter.id}/readings`
      && row.generation === generation);
    assert.ok(observed.length, 'History must be fetched in the new document');
    assert.deepEqual(await observed.at(-1).json, expected, 'Persisted original reading rows must remain exact');
  }

  const common = await createMeter(`${tag} ALLGEMEIN`, null);
  await openHistory(common);
  await button(panel(), 'Stand erfassen').click();
  await field(panel(), 'Ablesedatum *').fill('2026-01-01');
  await field(panel(), 'Zählerstand *').fill('1,00001');
  await button(panel(), 'Stand speichern').click();
  await panel().getByRole('alert').waitFor();
  assert.equal(await field(panel(), 'Zählerstand *').inputValue(), '1,00001');
  assert.equal(mutations.filter(r => /^\/api\/meters\/[^/]+\/readings$/.test(new URL(r.url()).pathname)).length, 0);
  await button(panel(), 'Abbrechen').click();
  console.log('PASS invalid five-place reading: visible error, retained input, no POST');
  const commonRows = [
    await addReading(common, '2026-01-01', '100,0000', '100.0000'),
    await addReading(common, '2026-12-31', '250,1250', '250.1250'),
  ];
  await persisted(common, commonRows, '150,1250');
  console.log('PASS property meter: exact 100.0000 -> 250.1250, consumption 150.1250 after reload');

  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  const createdUnit = await uiResponse(page, 'POST', '/api/units', () => button(page, 'Speichern').click());
  const unitId = createdUnit.data.id;
  await page.getByRole('link', { name: unitName, exact: true }).click();
  await panel().waitFor();
  assert.equal(await panel().getByRole('cell', { name: common.meter_number, exact: true }).count(), 0,
    'Unit panel must not leak the common-area meter');
  const individual = await createMeter(`${tag} WOHNUNG`, unitId);
  await openHistory(individual);
  const unitRows = [
    await addReading(individual, '2026-01-01', '0,0000', '0.0000'),
    await addReading(individual, '2026-12-31', '12,5000', '12.5000'),
  ];
  await persisted(individual, unitRows, '12,5000');
  assert.equal(await panel().getByRole('cell', { name: common.meter_number, exact: true }).count(), 0);
  console.log('PASS unit meter: fixed property/unit, exact history and consumption 12.5000 after reload');

  await page.goto(propertyUrl);
  await panel().getByRole('cell', { name: common.meter_number, exact: true }).waitFor();
  await panel().getByRole('cell', { name: individual.meter_number, exact: true }).waitFor();
  const meterWrites = mutations.filter(r => new URL(r.url()).pathname === '/api/meters' && r.method() === 'POST');
  const readingWrites = mutations.filter(r => /^\/api\/meters\/[^/]+\/readings$/.test(new URL(r.url()).pathname) && r.method() === 'POST');
  assert.equal(meterWrites.length, 2);
  assert.equal(readingWrites.length, 4);
  assert.equal(mutations.filter(r => /\/(payments|leases|bank-transactions|settlements|cost-entries)(\/|$)/.test(new URL(r.url()).pathname)).length, 0);
  phase = 'logging-out';
  await button(page, 'Abmelden').click();
  await field(page, 'E-Mail').waitFor();
  const permits = [...authResponses];
  for (const url of authConsole) {
    const index = permits.indexOf(url);
    assert.notEqual(index, -1, `No matching allowed anonymous session response for ${url}`);
    permits.splice(index, 1);
  }
  assert.equal(permits.length, 0, 'Expected session responses and console messages must match one to one');
  assert.deepEqual(errors, []);
  await context.close();
  await browser.close();
  browser = null;
  console.log('PASS T30: UI-only setup, two meters/four readings, persistent exact values, no unexpected HTTP/console/page errors');
})().catch(async error => {
  console.error(error);
  if (browser) await browser.close();
  process.exitCode = 1;
});
