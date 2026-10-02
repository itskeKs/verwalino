const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base = 'http://127.0.0.1:13000';
const output = '/tmp/hv-b1-declaration-principal-acceptance';
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const report = { checks: [], pageErrors: [] };
  page.on('pageerror', error => report.pageErrors.push(error.message));
  let job, revoked = false;
  const control = action => execFileSync('backend/.venv/bin/python', ['scripts/acceptance/declaration_principal_control.py', job, action],
    { env: { ...process.env, PYTHONPATH: 'backend' }, encoding: 'utf8' });
  const login = async email => {
    await page.goto(base + '/login');
    await page.getByLabel('E-Mail').fill(email); await page.getByLabel('Passwort').fill('testpass1234');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    await page.waitForURL(url => !url.pathname.includes('login'));
  };
  try {
    await login('clerk@alpha.de');
    await page.goto(base + '/teilungserklaerung/import');
    await page.waitForURL(/\/teilungserklaerung\/import\/[0-9a-f-]+$/);
    job = new URL(page.url()).pathname.split('/').pop();
    const endpoint = `/api/declaration-imports/${job}`;
    await page.locator('#declaration-files:enabled').waitFor();
    await page.getByLabel('PDF-Dateien auswählen', { exact: true }).setInputFiles({ name: 'synthetic.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF') });
    await page.getByRole('button', { name: 'Ausgewählte PDFs hochladen', exact: true }).click();
    await page.getByRole('button', { name: 'Import starten', exact: true }).waitFor();
    const keys = []; let receipt;
    await page.route('**' + endpoint + '/start', async route => {
      keys.push(route.request().headers()['idempotency-key']);
      const response = await route.fetch(); assert.equal(response.status(), 200);
      receipt = (await response.json()).queue_request_id;
      if (keys.length === 1) await route.abort('failed'); else await route.fulfill({ response });
    });
    await page.getByRole('button', { name: 'Import starten', exact: true }).click();
    await page.getByText('Der Vorgang konnte nicht abgeschlossen werden.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Import starten', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Import starten' && button.disabled));
    assert.equal(keys.length, 2); assert.ok(keys[0]); assert.equal(keys[0], keys[1]);
    const queued = await (await page.request.get(base + endpoint)).json();
    assert.equal(queued.status, 'QUEUED'); assert.equal(queued.queue_request_id, receipt);
    report.checks.push('UI upload and start persist a queue receipt; a lost committed response reuses the same command key');
    await page.unroute('**' + endpoint + '/start');
    control('revoke'); revoked = true;
    assert.equal((await page.request.get(base + endpoint)).status(), 403);
    control('restore'); revoked = false;
    const cancelled = await (await page.request.get(base + endpoint)).json();
    assert.equal(cancelled.status, 'CANCELLED'); assert.equal(cancelled.error_code, 'JOB_PERMISSION_CHANGED');
    await page.reload();
    await page.getByText(/Berechtigung des auslösenden Benutzers gilt nicht mehr/).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
    await page.getByRole('button', { name: 'Erneut versuchen', exact: true }).scrollIntoViewIfNeeded();
    report.mobile = await page.locator('main.declaration-import-page').evaluate(node => ({ visible: node.clientWidth, content: node.scrollWidth }));
    await fs.writeFile(output + '/mobile-geometry.json', JSON.stringify(await page.locator('main.declaration-import-page').evaluate(node => ({box:node.getBoundingClientRect().toJSON(), overflow:[...node.querySelectorAll('*')].filter(n=>n.getBoundingClientRect().right>node.getBoundingClientRect().right+1).map(n=>({tag:n.tagName, cls:n.className, width:n.getBoundingClientRect().width}))})), null, 2));
    assert.ok(report.mobile.content <= report.mobile.visible + 1, JSON.stringify(report.mobile));
    await page.screenshot({ path: output + '/permission-cancelled-mobile.png' });
    const retriedResponse = page.waitForResponse(response => new URL(response.url()).pathname === endpoint + '/retry');
    await page.getByRole('button', { name: 'Erneut versuchen', exact: true }).click();
    const retried = await retriedResponse; assert.equal(retried.status(), 200);
    const next = await retried.json(); assert.notEqual(next.queue_request_id, receipt); assert.equal(next.status, 'QUEUED');
    const replay = await page.request.post(base + endpoint + '/start', { headers: { 'Idempotency-Key': keys[0] } });
    assert.equal(replay.status(), 200);
    const replayed = await replay.json(); assert.equal(replayed.command_receipt_id, receipt); assert.equal(replayed.queue_request_id, next.queue_request_id);
    report.checks.push('revocation durably cancels through the production fenced helper without provider access; UI explains cancellation and explicit retry creates a new receipt');
    report.checks.push('old command replay acknowledges its original receipt without replacing the current retry principal');
    assert.deepEqual(report.pageErrors, []);
    report.status = 'passed';
    await fs.writeFile(output + '/report.json', JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    await page.screenshot({ path: output + "/failure.png" });
    throw error;
  } finally {
    if (revoked) control('restore');
    if (job) await page.request.post(base + `/api/declaration-imports/${job}/cancel`);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
