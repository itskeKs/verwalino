/* Local synthetic scoped creation acceptance; API18000/Vite13000/DB55432 only. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const output = process.env.HV_ACCEPTANCE_OUTPUT || '/tmp/hv-b1-creation-acceptance';
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const report = { checks: [], pageErrors: [], failedResponses: [], blockedLegacyOverviews: 0 };
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => report.pageErrors.push(error.message));
    page.on('response', response => { if(response.status() === 409 && response.url().includes('/api/fristen?')) { report.blockedLegacyOverviews++; return; } if (response.status() >= 400 && !(response.status() === 401 && response.url().endsWith('/api/auth/session'))) report.failedResponses.push([response.status(), response.url()]); });
    await page.goto(base + '/login');
    await page.getByLabel('E-Mail').fill('admin@alpha.de');
    await page.getByLabel('Passwort').fill('testpass1234');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    await page.waitForURL(url => !url.pathname.includes('login'));
    const post = async (url, data, keyed=false) => {
      const response = await page.request.post(base + url, { data, headers: keyed ? { 'Idempotency-Key': randomUUID() } : {} });
      assert.equal(response.status(), 201, await response.text()); return response.json();
    };
    const admin = (await (await page.request.get(base+'/api/users/page?eligible_for_scope=true&q=admin%40alpha.de')).json()).items[0];
    const stamp = Date.now();
    const property = await post('/api/properties', { name: 'Creation scope '+stamp, street: 'Test 1', zip_code: '12345', city: 'Test', management_type: 'MISCH' });
    const other = await post('/api/properties', { name: 'Other creation '+stamp, street: 'Test 2', zip_code: '12345', city: 'Test', management_type: 'MISCH' });
    const makeScope = (label, kind, propertyId=property.id) => post('/api/accounting-scopes', { property_id: propertyId, scope_type: kind, label, valid_from: '2025-01-01', whole_property: true, units: [] }, true);
    const rental = await makeScope('Creation rental', 'MIET');
    const weg = await makeScope('Creation WEG', 'WEG');
    const unavailable = await makeScope('Configuration without grant', 'WEG');
    const otherScope = await makeScope('Other rental', 'MIET', other.id);
    for (const scope of [rental, weg, otherScope]) await post(`/api/accounting-scopes/${scope.id}/grants`, { user_id: admin.id, capabilities: ['read','prepare'], reason: 'Synthetic creation acceptance' }, true);
    const selectScope = async (id) => {
      await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${id}"]`).waitFor({ state: 'attached' });
      await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).selectOption(id);
    };
    await page.goto(base+'/bankkonten');
    await page.getByRole('button', { name: '+ Neues Bankkonto', exact: true }).click();
    await page.getByRole('group', { name: 'Objekt *', exact: true }).getByLabel('Objekte durchsuchen', { exact: true }).fill(property.name);
    await page.locator('form select').first().locator(`option[value="${property.id}"]`).waitFor({ state: 'attached' });
    await page.locator('form select').first().selectOption(property.id);
    await selectScope(weg.id);
    await page.locator('form select').nth(1).selectOption('KAUTION');
    await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${rental.id}"]`).waitFor({ state: 'attached' });
    assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).inputValue(), '');
    assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${weg.id}"]`).count(), 0);
    await page.locator('form select').nth(1).selectOption('LAUFEND');
    await selectScope(rental.id);
    assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${unavailable.id}"]`).count(), 0);
    report.checks.push('account type change clears scope and restricts deposit accounts to rental scopes');
    await page.getByRole('group', { name: 'Objekt *', exact: true }).getByLabel('Objekte durchsuchen', { exact: true }).fill(other.name);
    await page.locator('form select').first().locator(`option[value="${other.id}"]`).waitFor({ state: 'attached' });
    await page.locator('form select').first().selectOption(other.id);
    await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${otherScope.id}"]`).waitFor({ state: 'attached' });
    assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).inputValue(), '');
    await page.getByRole('group', { name: 'Objekt *', exact: true }).getByLabel('Objekte durchsuchen', { exact: true }).fill(property.name);
    await page.locator('form select').first().locator(`option[value="${property.id}"]`).waitFor({ state: 'attached' });
    await page.locator('form select').first().selectOption(property.id);
    await selectScope(rental.id);
    await page.getByLabel('Bezeichnung *', { exact: true }).fill('Created scoped bank '+stamp);
    const bankSaved = page.waitForResponse(r => r.url().endsWith('/api/bank-accounts') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Speichern', exact: true }).click();
    assert.equal((await bankSaved).status(), 201);
    report.checks.push('bank creation submits exact scope; property switch clears scope; admin configuration without grant excluded');
    for (const [route, button, propertyLabel, expected] of [
      ['/kosten','+ Neue Kostenposition','Objekt *',rental.id],
      ['/abrechnungen','+ Neue Abrechnung','Objekt *',rental.id],
      ['/wirtschaftsplaene','+ Neuer Wirtschaftsplan','WEG-Objekt *',weg.id],
      ['/vermoegensberichte','+ Neuer Vermögensbericht','WEG-Objekt *',weg.id],
    ]) {
      await page.goto(base+route); await page.getByRole('button', { name: button, exact: true }).click();
      await page.locator('form select').first().selectOption(property.id); await selectScope(expected);
      if (route !== '/kosten') assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).locator(`option[value="${expected===weg.id ? rental.id : weg.id}"]`).count(), 0);
      report.checks.push(route+' offers permitted accounting type');
    }
    const uploaded = await page.request.post(base+'/api/invoice-documents/upload', { multipart: { file: { name: 'scope.pdf', mimeType: 'application/pdf', buffer: await fs.readFile(process.env.HV_SCOPE_INVOICE_FIXTURE || '/tmp/hv-scope-creation.pdf') } } });
    assert.equal(uploaded.status(), 201); const document = await uploaded.json();
    await page.goto(base+'/belege/'+document.id);
    await page.getByRole('button', { name: 'Kostenposition erstellen', exact: true }).click();
    await page.locator('form select').first().selectOption(property.id);
    const categories = await (await page.request.get(base+'/api/cost-categories')).json();
    await page.locator('form select').nth(1).selectOption(categories.find(c=>c.code==='GRUNDSTEUER').id);
    for (const label of ['Rechnungsdatum *','Leistungszeitraum von *']) await page.getByLabel(label, { exact: true }).fill('2025-01-01');
    await page.getByLabel('Leistungszeitraum bis *', { exact: true }).fill('2025-12-31');
    await page.getByLabel('Bruttobetrag *', { exact: true }).fill('119.00');
    await selectScope(rental.id);
    let created, key;
    await page.route('**/api/cost-entries', async route => {
      if (route.request().method() !== 'POST') return route.continue();
      const body = route.request().postDataJSON(); assert.equal(body.accounting_scope_id, rental.id);
      key = route.request().headers()['idempotency-key']; const response = await route.fetch();
      assert.equal(response.status(), 201); created = await response.json();
      await route.abort('failed'); await page.unroute('**/api/cost-entries');
    });
    await page.getByRole('button', { name: 'Entwurf erstellen', exact: true }).click();
    await page.locator('.error-text').filter({ hasText: /fetch|network|Failed/i }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: 'Abrechnungskreis', exact: true }).inputValue(), rental.id);
    const retry = page.waitForResponse(r=>r.url().endsWith('/api/cost-entries') && r.request().method()==='POST');
    await page.getByRole('button', { name: 'Entwurf erstellen', exact: true }).click();
    const response = await retry; assert.equal((await response.json()).id, created.id); assert.equal(response.request().headers()['idempotency-key'], key);
    await page.waitForURL('**/kosten/'+created.id);
    report.checks.push('upload-first scoped cost lost reply replays same key/result and retains scope selection');
    await page.goto(base+'/bankkonten'); await page.getByRole('button', { name: '+ Neues Bankkonto', exact: true }).click();
    await page.getByRole('group', { name: 'Objekt *', exact: true }).getByLabel('Objekte durchsuchen', { exact: true }).fill(property.name);
    await page.locator('form select').first().locator(`option[value="${property.id}"]`).waitFor({ state:'attached' });
    await page.locator('form select').first().selectOption(property.id); await selectScope(rental.id);
    await page.setViewportSize({ width:390, height:844 });
    await page.waitForFunction(()=>document.querySelector('aside').getBoundingClientRect().right<=1);
    await page.getByRole('combobox', { name:'Abrechnungskreis', exact:true }).scrollIntoViewIfNeeded();
    report.mobile = await page.locator('main').evaluate(n=>({ visible:n.clientWidth, content:n.scrollWidth }));
    assert.ok(report.mobile.content <= report.mobile.visible+1);
    await page.screenshot({ path:output+'/creation-mobile.png' });
    assert.deepEqual(report.pageErrors,[]); assert.deepEqual(report.failedResponses,[]); report.status='passed';
  } finally {
    if (page && report.status!=='passed') await page.screenshot({ path:output+'/failed-state.png', fullPage:true });
    await fs.writeFile(output+'/browser-report.json', JSON.stringify(report,null,2)); await browser.close();
  }
  console.log(JSON.stringify(report));
})().catch(e=>{ console.error(e); process.exitCode=1; });
