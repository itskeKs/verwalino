#!/usr/bin/env node
/* T43: UI-only synthetic setup against scripts/dev-local.sh (:13000/:18000).
 * No fixtures, SQL, direct API mutations, output/report files or production access.
 * Requires local Playwright and Chrome. Synthetic records intentionally remain.
 */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T43 ${Date.now()}`;
const property = `${prefix} Mietobjekt`, unit = `${prefix} Wohnung`, person = `${prefix} Ada Synthetisch`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
let browser;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], staffelRequests = [];
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/rent-changes/staffel')) staffelRequests.push(r); });
  await page.goto(base); await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword'); await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte'); await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property); await page.getByLabel('Verwaltungsart').selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Staffelweg 43'); await field(page, 'PLZ').fill('12345'); await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check(); await field(page, 'Begründung der Freigabe').fill('T43 synthetische lokale UI-Abnahme');
  await button(page, 'Speichern').click();
  await field(page, 'Objekte suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: property, exact: true }).click(); await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unit); await field(page, 'Flaeche (m2)').fill('70'); await button(page, 'Speichern').click();
  await page.getByRole('link', { name: unit, exact: true }).click(); const unitUrl = page.url();
  await page.goto(base + '/personen'); await button(page, '+ Neue Person').click();
  await field(page, 'Vorname').fill(`${prefix} Ada`); await field(page, 'Nachname *').fill('Synthetisch'); await button(page, 'Speichern').click();
  await field(page, 'Personen suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: person, exact: true }).click(); await button(page, 'Als Mieter kennzeichnen').click();
  await page.getByText('Mieter', { exact: true }).waitFor(); await page.goto(unitUrl); await button(page, '+ Mietvertrag').click();
  const tenant = field(page, 'Mieter *'); await tenant.getByRole('option', { name: person, exact: true }).waitFor({ state: 'attached' }); await tenant.selectOption({ label: person });
  await field(page, 'Vertragsbeginn *').fill('2026-01-01'); await field(page, 'Kaltmiete (EUR) *').fill('700.00'); await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
  const created = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/leases');
  await button(page, 'Speichern').click(); const leaseResponse = await created; assert.equal(leaseResponse.status(), 201, await leaseResponse.text());
  const lease = await leaseResponse.json();
  const expected = [
    ['2026-01-01', '700.00', '150.00', 'Originalvertrag'],
    ['2027-01-01', '730.00', '150.00', 'Mietänderung'],
    ['2028-01-01', '760.00', '150.00', 'Mietänderung'],
    ['2029-01-01', '790.00', '150.00', 'Mietänderung'],
    ['2030-01-01', '820.00', '150.00', 'Mietänderung'],
    ['2031-01-01', '850.00', '150.00', 'Mietänderung'],
  ];
  async function openStaffel() {
    await button(page, 'Staffelmiete anlegen').click();
    const dialog = page.getByRole('dialog', { name: 'Staffelmiete anlegen', exact: true });
    await field(dialog, 'Erste Erhöhung ab').fill('2027-01-01'); await field(dialog, 'Erhöhung (EUR)').fill('30,00');
    await field(dialog, 'Intervall (Monate)').fill('12'); await field(dialog, 'Anzahl Erhöhungen').fill('5'); return dialog;
  }
  let dialog = await openStaffel();
  await field(dialog, 'Intervall (Monate)').fill('11'); await button(dialog, 'Staffelmiete speichern').click();
  await dialog.getByRole('alert').waitFor(); assert.equal(staffelRequests.length, 0);
  await field(dialog, 'Intervall (Monate)').fill('12');
  const submitted = page.waitForResponse(r => r.url().endsWith(`/leases/${lease.id}/rent-changes/staffel`) && r.request().method() === 'POST');
  await button(dialog, 'Staffelmiete speichern').click(); const res = await submitted; assert.equal(res.status(), 201, await res.text());
  assert.equal((await res.json()).length, 5); await dialog.waitFor({ state: 'hidden' });
  assert.equal(staffelRequests.length, 1);
  assert.deepEqual(staffelRequests[0].postDataJSON(), { staffel_start_date: '2027-01-01', staffel_increase_amount: '30.00', staffel_interval_months: 12, staffel_steps: 5 });
  async function verifyHistory() {
    const table = page.getByRole('table', { name: /^Miethistorie:/ });
    await table.getByRole('cell', { name: '850.00', exact: true }).waitFor();
    const rows = await table.locator('tbody tr').all(); assert.equal(rows.length, 6);
    for (let i = 0; i < rows.length; i++) assert.deepEqual((await rows[i].getByRole('cell').allTextContents()).map(s => s.trim()), expected[i]);
  }
  await verifyHistory(); await page.reload(); await verifyHistory();
  dialog = await openStaffel(); const rejected = page.waitForResponse(r => r.url().endsWith('/rent-changes/staffel') && r.status() === 409);
  await button(dialog, 'Staffelmiete speichern').click(); await rejected; await dialog.getByRole('alert').waitFor();
  assert.equal(await field(dialog, 'Erhöhung (EUR)').inputValue(), '30,00'); await button(dialog, 'Abbrechen').click();
  await page.reload(); await verifyHistory();
  await page.setViewportSize({ width: 390, height: 844 }); await button(page, 'Staffelmiete anlegen').click();
  await page.getByRole('dialog', { name: 'Staffelmiete anlegen', exact: true }).waitFor(); await button(page, 'Abbrechen').click();
  assert.deepEqual(errors, []);
  console.log('T43 UI PASS: synthetic UI setup; 700/150 original; +30 annually; five exact 2027–2031 steps; reload persistence; 11-month rejection; duplicate 409 without partial changes; phone dialog. No PDF/download surface in T43.');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
