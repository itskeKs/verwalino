const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base = 'http://127.0.0.1:13000', output = '/tmp/hv-b1-control-acceptance';
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const report = { checks: [], pageErrors: [], responses: [] };
  page.on('pageerror', e => report.pageErrors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) report.responses.push({ status: r.status(), path: new URL(r.url()).pathname }); });
  try {
    await page.goto(base + '/login');
    await page.getByLabel('E-Mail').fill('admin@alpha.de');
    await page.getByLabel('Passwort').fill('testpass1234');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    await page.waitForURL(u => !u.pathname.includes('login'));
    const post = async (path, data, keyed = false, status = 201) => {
      const r = await page.request.post(base + path, { data, headers: keyed ? { 'Idempotency-Key': randomUUID() } : {} });
      assert.equal(r.status(), status, await r.text()); return status === 204 ? null : r.json();
    };
    const admin = (await (await page.request.get(base + '/api/users/page?eligible_for_scope=true&q=admin%40alpha.de')).json()).items[0];
    const property = await post('/api/properties', { name: 'Scoped control QA ' + Date.now(), street: 'Test 1', zip_code: '12345', city: 'Test', management_type: 'MIET' });
    const scope = await post('/api/accounting-scopes', { property_id: property.id, scope_type: 'MIET', label: 'Scoped control QA', valid_from: '2025-01-01', whole_property: true, units: [] }, true);
    const grant = await post(`/api/accounting-scopes/${scope.id}/grants`, { user_id: admin.id, capabilities: ['read', 'prepare', 'export'], reason: 'Synthetic control grant' }, true);
    const bankData = { property_id: property.id, accounting_scope_id: scope.id, account_type: 'LAUFEND', label: 'Control QA bank' };
    const bank = await post('/api/bank-accounts', bankData);
    const original = await (await page.request.get(base + '/api/bank-accounts/' + bank.id)).json();
    await page.goto(base + '/berechtigungen/teamrechte/' + scope.id);
    await page.getByText('Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv', { exact: true }).waitFor();
    await page.getByText(/keine vollständige Buchungssperre/).waitFor();
    const reason = page.getByLabel('Begründung der Betriebsänderung', { exact: true });
    await reason.fill('Bereichsbezogene Arbeiten zur Prüfung pausieren');
    const endpoint = '/api/accounting-scopes/control', posts = [];
    let committed;
    await page.route('**' + endpoint, async route => {
      if (route.request().method() !== 'POST') { await route.continue(); return; }
      posts.push({ key: route.request().headers()['idempotency-key'], body: route.request().postData() });
      if (posts.length === 1) {
        const result = await route.fetch(); assert.equal(result.status(), 201, await result.text()); committed = await result.json();
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Antwort nach erfolgreicher Pause unterbrochen' }) });
      } else await route.continue();
    });
    const pause = page.getByRole('button', { name: 'Bereichsbezogene Finanzaktionen pausieren', exact: true });
    await pause.click();
    await page.getByRole('alert').filter({ hasText: 'Antwort nach erfolgreicher Pause unterbrochen' }).waitFor();
    await pause.click();
    await page.getByText('Bereichsbezogene Finanzaktionen pausiert', { exact: true }).waitFor();
    assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);
    assert.equal((await page.request.post(base + '/api/bank-accounts', { data: bankData })).status(), 404);
    assert.deepEqual(await (await page.request.get(base + '/api/bank-accounts/' + bank.id)).json(), original);
    report.checks.push('scoped pause keeps exact read access and original bank data while blocking new scoped effects; UI explicitly describes legacy compatibility');
    report.checks.push('lost committed response is retried with the same command and key, and the UI reloads the live state');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
    await page.getByRole('region', { name: 'Bereichsbezogene Finanzaktionen' }).scrollIntoViewIfNeeded();
    report.mobile = await page.locator('main').evaluate(n => ({ visible: n.clientWidth, content: n.scrollWidth }));
    assert.ok(report.mobile.content <= report.mobile.visible + 1);
    await page.screenshot({ path: output + '/control-mobile.png' });
    await post(`/api/accounting-scopes/${scope.id}/grants/${grant.id}/revoke`, {}, false, 204);
    await reason.fill('Prüfung abgeschlossen, nur aktuelle Rechte verwenden');
    await page.getByRole('button', { name: 'Bereichsbezogene Finanzaktionen wieder aufnehmen', exact: true }).click();
    await page.getByText('Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv', { exact: true }).waitFor();
    assert.equal((await page.request.post(base + '/api/bank-accounts', { data: bankData })).status(), 404);
    assert.equal((await page.request.get(base + '/api/bank-accounts/' + bank.id)).status(), 404);
    const replay = await page.request.post(base + endpoint, { data: JSON.parse(posts[0].body), headers: { 'Idempotency-Key': posts[0].key } });
    assert.equal(replay.status(), 201); assert.deepEqual(await replay.json(), committed);
    const current = await (await page.request.get(base + endpoint)).json();
    assert.equal(current.mode, 'compatibility'); assert.notEqual(current.event.id, committed.id);
    report.checks.push('resume retains revoked financial permissions; replay of the old pause returns its receipt without pausing the current state');
    assert.deepEqual(report.pageErrors, []);
    assert.ok(report.responses.every(r => (r.status === 401 && r.path === '/api/auth/session') || (r.status === 409 && r.path === '/api/fristen') || (r.status === 503 && r.path === endpoint)), JSON.stringify(report.responses));
    report.status = 'passed';
  } finally {
    if (report.status !== 'passed') await page.screenshot({ path: output + '/failed-state.png', fullPage: true });
    await fs.writeFile(output + '/browser-report.json', JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify(report));
})().catch(e => { console.error(e); process.exitCode = 1; });
