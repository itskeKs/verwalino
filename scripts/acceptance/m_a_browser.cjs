/* Bounded local synthetic M-A browser journey. See m-a-repaired-baseline.md.
 * Requires an existing Playwright package/browser; never installs dependencies.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const output = process.env.HV_ACCEPTANCE_OUTPUT || '/tmp/hv-ma-acceptance';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const report = { base, viewports: ['1440x1000', '390x844'], journeys: [], console: [], pageErrors: [] };
let browser, page;
async function shot(name) { await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); }
async function responseAfter(pattern, action) {
  const pending = page.waitForResponse(r => r.url().endsWith(pattern) && r.request().method() === 'POST');
  await action(); return pending;
}
async function login(context, email) {
  const tab = await context.newPage();
  await tab.goto(base + '/login');
  await tab.getByLabel('E-Mail').fill(email);
  await tab.getByLabel('Passwort').fill('testpass1234');
  await tab.getByRole('button', { name: 'Anmelden', exact: true }).click();
  await tab.waitForURL(url => !url.pathname.includes('login'));
  return tab;
}
async function createRun(fixture, amount) {
  await page.goto(base + '/abrechnungen');
  await page.getByRole('button', { name: '+ Neue Abrechnung', exact: true }).click();
  await page.getByLabel(/^Objekt \*/).selectOption(fixture.prop_b);
  await page.getByLabel(/^Mietvertrag \*/).selectOption(fixture.lease_d);
  await page.getByLabel('Abrechnungszeitraum von *', { exact: true }).fill('2025-01-01');
  await page.getByLabel('Abrechnungszeitraum bis *', { exact: true }).fill('2025-12-31');
  if (amount !== null) await page.getByLabel('Vorauszahlungssumme', { exact: true }).fill(amount);
  const created = await responseAfter('/api/settlements', () => page.getByRole('button', { name: 'Anlegen', exact: true }).click());
  assert.equal(created.status(), 201, await created.text());
  const run = await created.json();
  await page.waitForURL(base + '/abrechnungen/' + run.id);
  return run.id;
}
async function viewportChecks(url) {
  await page.goto(url);
  await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await shot('desktop-final');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
  const widths = await page.locator('main').evaluate(el => ({ visible: el.clientWidth, content: el.scrollWidth }));
  assert.ok(widths.content <= widths.visible + 1, 'Account page must fit the mobile viewport');
  await shot('mobile-final');
  await page.getByRole('button', { name: 'Navigation umschalten', exact: true }).click();
  await page.getByRole('link', { name: 'Belege', exact: true }).click();
  await page.waitForURL(base + '/belege');
  await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
  await shot('mobile-navigation');
  return { ...widths, navigation: 'passed' };
}
(async () => {
  const fixture = JSON.parse(await fs.readFile(path.join(output, 'fixture.json')));
  browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await login(context, 'admin@alpha.de');
  page.setDefaultTimeout(15000);
  if (process.argv.includes('--viewport-only')) {
    const previous = JSON.parse(await fs.readFile(path.join(output, 'browser-report.json')));
    const viewport = await viewportChecks(previous.url);
    await fs.writeFile(path.join(output, 'viewport-report.json'), JSON.stringify(viewport, null, 2));
    console.log(JSON.stringify({ status: 'passed', viewport }));
    return;
  }
  page.on('dialog', dialog => dialog.accept());
  page.on('pageerror', error => report.pageErrors.push(error.message));
  page.on('console', msg => { if (['warning', 'error'].includes(msg.type())) report.console.push(msg.text().slice(0, 500)); });
  const categories = await (await context.request.get(base + '/api/cost-categories')).json();
  const category = categories.find(c => c.code === 'GRUNDSTEUER' && c.plan_version === 1);
  const completed = [];
  for (const [filename, amount] of [['text.pdf', '119.00'], ['scan.pdf', '119.00'], ['invoice.xml', '119.00'], ['embedded.pdf', '476.00']]) {
    await page.goto(base + '/belege');
    await page.locator('input[type=file]').setInputFiles(path.join(output, filename));
    const uploaded = await responseAfter('/api/invoice-documents/upload', () => page.getByRole('button', { name: 'Hochladen', exact: true }).click());
    assert.equal(uploaded.status(), 201, await uploaded.text());
    const document = await uploaded.json();
    assert.equal(document.cost_entry_id, null);
    await page.locator(`a[href="/belege/${document.id}"]`).click();
    await page.waitForURL(base + '/belege/' + document.id);
    await page.getByRole('heading', { name: 'Beleg: ' + filename + ' active', exact: true }).waitFor();
    await page.getByRole('status').filter({ hasText: 'Felder vorbereitet' }).waitFor({ timeout: 45000 });
    assert.equal(await page.locator('vite-error-overlay').count(), 0);
    await shot(filename.replace('.', '-') + '-extracted');
    const current = await (await context.request.get(base + '/api/invoice-documents/' + document.id)).json();
    assert.equal(current.extraction_payload.gross_amount, amount);
    const original = await context.request.get(base + '/api/invoice-documents/' + document.id + '/content');
    const bytes = await original.body();
    assert.equal(sha(bytes), sha(await fs.readFile(path.join(output, filename))));
    assert.equal(sha(bytes), document.content_sha256);
    await page.getByRole('button', { name: 'Kostenposition erstellen', exact: true }).click();
    await page.getByLabel(/^Objekt \*/).selectOption(fixture.prop_b);
    await page.getByLabel(/^Kategorie \*/).selectOption(category.id);
    await page.getByLabel('Rechnungsdatum *', { exact: true }).fill('2025-06-01');
    await page.getByLabel('Leistungszeitraum von *', { exact: true }).fill('2025-01-01');
    await page.getByLabel('Leistungszeitraum bis *', { exact: true }).fill('2025-12-31');
    assert.equal(await page.getByLabel('Bruttobetrag *', { exact: true }).inputValue(), amount);
    let uncertainResult, firstKey;
    if (filename === 'text.pdf') {
      await page.route('**/api/cost-entries', async route => {
        if (route.request().method() !== 'POST') return route.continue();
        firstKey = route.request().headers()['idempotency-key'];
        const real = await route.fetch(); assert.equal(real.status(), 201, await real.text());
        uncertainResult = await real.json();
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic response interruption; retry the saved draft' }) });
      }, { times: 1 });
      const interrupted = await responseAfter('/api/cost-entries', () => page.getByRole('button', { name: 'Entwurf erstellen', exact: true }).click());
      assert.equal(interrupted.status(), 503);
      await page.getByText('Synthetic response interruption; retry the saved draft', { exact: true }).waitFor();
      assert.equal(await page.getByLabel(/^Objekt \*/).inputValue(), fixture.prop_b);
      await shot('retry-retains-input');
    }
    const created = await responseAfter('/api/cost-entries', () => page.getByRole('button', { name: 'Entwurf erstellen', exact: true }).click());
    assert.equal(created.status(), 201, await created.text());
    const cost = await created.json();
    if (uncertainResult) { assert.equal(cost.id, uncertainResult.id); assert.equal(created.request().headers()['idempotency-key'], firstKey); }
    await page.waitForURL(base + '/kosten/' + cost.id);
    const confirmed = await responseAfter('/api/cost-entries/' + cost.id + '/confirm', () => page.getByRole('button', { name: 'Bestaetigen', exact: true }).click());
    assert.equal(confirmed.status(), 200, await confirmed.text());
    await page.reload();
    await page.getByRole('heading', { name: 'Verknuepfte Belege', exact: true }).waitFor();
    await page.getByRole('link', { name: 'Ansehen', exact: true }).click();
    const released = await (await context.request.get(base + '/api/invoice-documents/' + document.id)).json();
    assert.equal(released.cost_entry_id, cost.id); assert.ok(released.released_at);
    console.log('Confirmed source: ' + filename);
    completed.push({ filename, source: document.id, cost: cost.id, hash: document.content_sha256, amount });
  }
  report.journeys.push({ name: 'four source formats through upload/review/confirm/source', completed, uncertainSaveReplay: true });
  await page.goto(base + '/belege');
  await page.locator('input[type=file]').setInputFiles(path.join(output, 'encrypted.pdf'));
  const failureUpload = await responseAfter('/api/invoice-documents/upload', () => page.getByRole('button', { name: 'Hochladen', exact: true }).click());
  const failedDoc = await failureUpload.json();
  await page.locator(`a[href="/belege/${failedDoc.id}"]`).click();
  await page.getByRole('button', { name: 'Texterkennung erneut starten', exact: true }).waitFor({ timeout: 45000 });
  const retry = await responseAfter('/api/invoice-documents/' + failedDoc.id + '/retry', () => page.getByRole('button', { name: 'Texterkennung erneut starten', exact: true }).click());
  assert.equal(retry.status(), 200);
  await page.getByRole('button', { name: 'Texterkennung erneut starten', exact: true }).waitFor({ timeout: 45000 });
  const failedState = await (await context.request.get(base + '/api/invoice-documents/' + failedDoc.id)).json();
  assert.equal(failedState.cost_entry_id, null); assert.equal(failedState.processing_error_code, 'encrypted_pdf');
  await shot('encrypted-source-retained');
  report.journeys.push({ name: 'encrypted failure and retry', originalRetained: true, noCostCreated: true });
  const unknownRun = await createRun(fixture, null);
  for (const action of ['compute', 'preview', 'finalize']) {
    const label = { compute: 'Berechnen', preview: 'Vorschau', finalize: 'Finalisieren' }[action];
    const blocked = await responseAfter('/api/settlements/' + unknownRun + '/' + action, () => page.getByRole('button', { name: label, exact: true }).click());
    assert.equal(blocked.status(), 422); assert.equal((await blocked.json()).detail.code, 'financial_inputs_incomplete');
  }
  await shot('unknown-blocked');
  const finals = [];
  for (const amount of ['0.00', '1200.00']) {
    const runId = await createRun(fixture, amount);
    const before = await responseAfter('/api/settlements/' + runId + '/finalize', () => page.getByRole('button', { name: 'Finalisieren', exact: true }).click());
    assert.equal(before.status(), 422);
    await page.getByLabel('Grundlage der Prüfung').fill('Synthetic complete bank records for 2025 checked; explicit amount ' + amount);
    await page.getByRole('checkbox').check();
    const attested = await responseAfter('/api/financial-input-coverage', () => page.getByRole('button', { name: 'Datengrundlage speichern', exact: true }).click());
    assert.equal(attested.status(), 201, await attested.text());
    const computed = await responseAfter('/api/settlements/' + runId + '/compute', () => page.getByRole('button', { name: 'Berechnen', exact: true }).click());
    assert.equal(computed.status(), 200, await computed.text());
    const draftPdf = await context.request.get(base + '/api/settlements/' + runId + '/pdf');
    assert.equal(draftPdf.status(), 409);
    const finalized = await responseAfter('/api/settlements/' + runId + '/finalize', () => page.getByRole('button', { name: 'Finalisieren', exact: true }).click());
    assert.equal(finalized.status(), 200, await finalized.text());
    const result = await finalized.json();
    assert.equal(result.total_cost, '2033.00'); // seed 1200 + 119 + 119 + 119 + 476
    assert.equal(result.balance, amount === '0.00' ? '2033.00' : '833.00');
    const popupPromise = page.waitForEvent('popup');
    await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).click();
    const popup = await popupPromise; await popup.waitForLoadState(); await popup.close();
    const pdf = await context.request.get(base + '/api/settlements/' + runId + '/pdf');
    assert.equal(pdf.status(), 200); assert.equal((await pdf.body()).subarray(0, 4).toString(), '%PDF');
    await fs.writeFile(path.join(output, 'settlement-' + amount + '.pdf'), await pdf.body());
    await shot('final-' + amount);
    const sourceLink = page.locator(`a[href="/kosten/${completed[0].cost}"]`).first();
    await sourceLink.click();
    await page.getByRole('link', { name: 'Ansehen', exact: true }).click();
    await page.getByRole('link', { name: 'Dokument ansehen', exact: true }).waitFor();
    finals.push({ run: runId, amount, totalCost: result.total_cost, balance: result.balance, snapshot: result.snapshot_sha256 });
  }
  report.journeys.push({ name: 'unknown/zero/1200 account, draft PDF guard, final PDF and source navigation', unknownRun, finals });
  const foreignContext = await browser.newContext();
  const foreignPage = await login(foreignContext, 'admin@beta.de');
  const denial = await foreignPage.goto(base + '/api/invoice-documents/' + completed[0].source + '/content');
  assert.equal(denial.status(), 404);
  assert.equal((await foreignContext.request.get(base + '/api/settlements/' + finals[0].run)).status(), 404);
  await foreignContext.close();
  report.journeys.push({ name: 'second organization denial', source: 404, settlement: 404 });
  await page.goto(base + '/abrechnungen/' + finals[0].run);
  await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).waitFor();
  report.title = await page.title(); report.url = page.url();
  assert.ok((await page.locator('main').innerText()).length > 100);
  assert.equal(await page.locator('vite-error-overlay').count(), 0);
  report.mobile = await viewportChecks(report.url);
  assert.deepEqual(report.pageErrors, []);
  assert.ok(report.console.every(message => /status of (422|503)/.test(message)), 'Unexpected browser warning/error');
  report.status = 'passed';
  await fs.writeFile(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, journeys: report.journeys.length, evidence: output }));
})().catch(async error => {
  report.status = 'failed'; report.failure = error.stack;
  console.error(error);
  if (page) { await shot('failure').catch(() => {}); await fs.writeFile(path.join(output, 'failure-dom.txt'), await page.locator('body').innerText()).catch(() => {}); }
  await fs.writeFile(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2)); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });
