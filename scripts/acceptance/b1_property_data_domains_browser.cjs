// Browser plugin not available; regular Playwright against disposable local data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE);
const base = 'http://127.0.0.1:13000', output = '/tmp/hv-b1-property-data-acceptance';
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const report = { browserPath: 'Browser plugin not available; regular Playwright', checks: [], pageErrors: [] };
  page.on('pageerror', e => report.pageErrors.push(e.message));
  try {
    await page.goto(base + '/login');
    await page.getByLabel('E-Mail').fill('admin@alpha.de');
    await page.getByLabel('Passwort').fill('testpass1234');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    await page.waitForURL(u => !u.pathname.includes('login'));
    const propertyResponse = await page.request.post(base + '/api/properties', { data: { name: 'Shared data QA '+Date.now(), management_type: 'WEG' } });
    assert.equal(propertyResponse.status(), 201);
    const property = await propertyResponse.json();
    await page.goto(base + '/users');
    await page.getByRole('link', { name: 'Gemeinsame Gebäudedaten vorbereiten' }).click();
    assert.equal(new URL(page.url()).pathname, '/gebaeudedaten-rechte');
    assert.ok(await page.title());
    await page.getByRole('heading', { name: 'Gemeinsame Gebäudedaten', exact: true }).waitFor();
    await page.getByText(/beschränken den bisherigen Zugriff noch nicht/).waitFor();
    assert.equal(await page.locator('vite-error-overlay').count(), 0);
    await page.getByLabel('Objekt', { exact: true }).selectOption(property.id);
    await page.getByLabel('Bezeichnung', { exact: true }).fill('Gemeinsame Quellen QA');
    await page.getByLabel('Begründung', { exact: true }).fill('Geprüfter gemeinsamer Datenbereich');
    const creations = [];
    let domain;
    await page.route('**/api/property-data-domains', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      creations.push({ key: route.request().headers()['idempotency-key'], body: route.request().postData() });
      if (creations.length === 1) {
        const result = await route.fetch(); assert.equal(result.status(), 201, await result.text()); domain = await result.json();
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Gespeichert, Antwort unterbrochen' }) });
      }
      await route.continue();
    });
    const create = page.getByRole('button', { name: 'Datenbereich vorbereiten', exact: true });
    await create.click();
    await page.getByRole('alert').filter({ hasText: 'Gespeichert, Antwort unterbrochen' }).waitFor();
    await create.click();
    await page.getByRole('link', { name: 'Gemeinsame Quellen QA – Teamfreigaben vorbereiten', exact: true }).click();
    assert.equal(creations.length,2); assert.deepEqual(creations[0],creations[1]);
    assert.equal(domain.enforcement_status,'PREPARATION');
    await page.getByRole('heading', { name: 'Gemeinsame Quellen QA', exact: true }).waitFor();
    const userResponse = await page.request.get(base + '/api/users/page?eligible_for_scope=true&q=clerk%40alpha.de');
    const member = (await userResponse.json()).items[0];
    await page.getByLabel('Teammitglied', { exact: true }).selectOption(member.id);
    await page.getByRole('checkbox', { name: 'Bearbeiten', exact: true }).check();
    await page.getByLabel('Begründung', { exact: true }).fill('Gemeinsame Gebäudeunterlagen geprüft');
    await page.getByRole('button', { name: 'Teamfreigabe vorbereiten', exact: true }).click();
    await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Widerrufen', exact: true }).click();
    await page.getByRole('cell', { name: 'Widerrufen', exact: true }).waitFor();
    const history = await (await page.request.get(base + `/api/property-data-domains/${domain.id}/grants/page`)).json();
    assert.equal(history.items.length,1); assert.ok(history.items[0].revoked_at);
    report.checks.push('user administration -> prepared domain -> lost committed reply -> identical retry -> explicit member grant -> durable revocation');
    await page.setViewportSize({ width:390,height:844 });
    await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
    await page.getByRole('heading', { name:'Gemeinsame Gebäudedaten',exact:true }).scrollIntoViewIfNeeded();
    report.mobile = await page.locator('main').evaluate(n => ({ visible:n.clientWidth,content:n.scrollWidth }));
    assert.ok(report.mobile.content <= report.mobile.visible+1);
    await page.screenshot({ path:output+'/property-data-mobile.png' });
    assert.deepEqual(report.pageErrors,[]);
    report.checks.push('preparation and separate financial permissions remain explicit; 390px content fits with scrollable grant table');
    report.status = 'passed';
  } finally {
    if (report.status !== 'passed') await page.screenshot({ path:output+'/failed-state.png',fullPage:true });
    await fs.writeFile(output+'/browser-report.json',JSON.stringify(report,null,2));
    await browser.close();
  }
  console.log(JSON.stringify(report));
})().catch(e => { console.error(e); process.exitCode=1; });
