#!/usr/bin/env node
/* T44: UI-only synthetic setup against scripts/dev-local.sh (:13000/:18000).
 * No fixtures, SQL, direct API mutations, output/report files or production access.
 * Requires local Playwright and Chrome. Synthetic records intentionally remain.
 */
'use strict';
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T44 ${Date.now()}`;
const property = `${prefix} Mietobjekt`, unit = `${prefix} Wohnung`, person = `${prefix} Ada Synthetisch`;
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
let browser;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], indexRequests = [];
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/rent-changes/index')) indexRequests.push(r); });
  await page.goto(base); await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword'); await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte'); await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property); await page.getByLabel('Verwaltungsart').selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Staffelweg 43'); await field(page, 'PLZ').fill('12345'); await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check(); await field(page, 'Begründung der Freigabe').fill('T44 synthetische lokale UI-Abnahme');
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
  await field(page, 'Vertragsbeginn *').fill('2026-01-01'); await field(page, 'Kaltmiete (EUR) *').fill('600.00'); await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
  const created = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/leases');
  await button(page, 'Speichern').click(); const leaseResponse = await created; assert.equal(leaseResponse.status(), 201, await leaseResponse.text());
  const lease = await leaseResponse.json();
  // Radix hides background content from role queries while the modal is open.
  const history = () => page.locator('table[aria-label^="Miethistorie:"]');
  await history().getByRole('cell', { name: 'Originalvertrag', exact: true }).waitFor();
  await button(page, 'Indexmiete berechnen').click();
  const dialog = page.getByRole('dialog', { name: 'Indexmiete berechnen', exact: true });
  await field(dialog, 'VPI alt').fill('0'); await field(dialog, 'VPI neu').fill('115');
  await field(dialog, 'Zugang der Erklärung').fill('2026-10-15'); await button(dialog, 'Berechnen').click();
  await dialog.getByRole('alert').waitFor(); assert.equal(indexRequests.length, 0);
  await field(dialog, 'VPI alt').fill('110');
  const tooSoon = page.waitForResponse(r => r.url().endsWith('/rent-changes/index/preview') && r.status() === 422);
  await button(dialog, 'Berechnen').click(); await tooSoon; await dialog.getByRole('alert').waitFor();
  assert.equal(await history().locator('tbody tr').count(), 1);
  await field(dialog, 'Zugang der Erklärung').fill('2026-11-15');
  const previewResponse = page.waitForResponse(r => r.url().endsWith('/rent-changes/index/preview') && r.status() === 200);
  await button(dialog, 'Berechnen').click(); const preview = await (await previewResponse).json();
  assert.deepEqual(preview, { valid_from: '2027-01-01', previous_valid_from: '2026-01-01', previous_base_rent: '600.00', base_rent: '627.27', advance_payment: '150.00' });
  await dialog.getByText('Neue Kaltmiete: 627.27 EUR', { exact: true }).waitFor();
  assert.equal(await history().locator('tbody tr').count(), 1, 'Preview must not create history');
  // Editing an input invalidates the preview; recalculation is explicit.
  await field(dialog, 'VPI neu').fill('116'); assert.equal(await button(dialog, 'Als Mietänderung übernehmen').count(), 0);
  await field(dialog, 'VPI neu').fill('115');
  const recalculated = page.waitForResponse(r => r.url().endsWith('/rent-changes/index/preview') && r.status() === 200);
  await button(dialog, 'Berechnen').click(); await recalculated;
  await button(dialog, 'Als Mietänderung übernehmen').waitFor();
  const adopted = page.waitForResponse(r => r.url().endsWith('/rent-changes/index') && r.request().method() === 'POST');
  await button(dialog, 'Als Mietänderung übernehmen').click(); const response = await adopted;
  assert.equal(response.status(), 201, await response.text());
  assert.equal((await response.json()).base_rent, '627.27'); await dialog.waitFor({ state: 'hidden' });
  const createRequests = indexRequests.filter(r => r.url().endsWith('/rent-changes/index'));
  assert.equal(createRequests.length, 1);
  assert.deepEqual(createRequests[0].postDataJSON(), { old_index: '110', new_index: '115', declaration_received_on: '2026-11-15', expected_base_rent: '627.27', expected_advance_payment: '150.00', expected_previous_valid_from: '2026-01-01' });
  async function verify() {
    await history().getByRole('cell', { name: '627.27', exact: true }).waitFor();
    const rows = await history().locator('tbody tr').all(); assert.equal(rows.length, 2);
    assert.deepEqual((await rows[0].getByRole('cell').allTextContents()).map(s => s.trim()), ['2026-01-01', '600.00', '150.00', 'Originalvertrag']);
    assert.deepEqual((await rows[1].getByRole('cell').allTextContents()).map(s => s.trim()), ['2027-01-01', '627.27', '150.00', 'Mietänderung']);
  }
  await verify(); await page.reload(); await verify();
  await button(page, 'Indexmiete berechnen').click();
  await field(dialog, 'VPI alt').fill('110'); await field(dialog, 'VPI neu').fill('115');
  await field(dialog, 'Zugang der Erklärung').fill('2026-11-15');
  const conflict = page.waitForResponse(r => r.url().endsWith('/rent-changes/index/preview') && r.status() === 409);
  await button(dialog, 'Berechnen').click(); await conflict; await dialog.getByRole('alert').waitFor();
  await button(dialog, 'Abbrechen').click(); await page.reload(); await verify();
  await page.setViewportSize({ width: 390, height: 844 }); await button(page, 'Indexmiete berechnen').click();
  await dialog.waitFor(); await button(dialog, 'Abbrechen').click();
  assert.deepEqual(errors, []);
  console.log('T44 UI PASS: synthetic 600/150 lease; VPI110→115 yields627.27 from2027-01-01; annual boundary422; preview nonmutating and invalidated by editing; explicit adoption; exact payload; original/persisted history after reload; duplicate409; phone dialog. No delivery assertion, PDF or download in T44.');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
