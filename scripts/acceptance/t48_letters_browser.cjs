#!/usr/bin/env node
/* T48 UI-only setup; retained PDFs are opened through actual UI links.
 * scripts/dev-local.sh, synthetic data only; no SQL, backend fixtures or API writes.
 */
'use strict';
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T48 ${Date.now()}`;
const property = `${prefix} Rundbriefhaus`;
const names = [`${prefix} Ada Synthetisch`, `${prefix} Berta Synthetisch`];
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, name) => scope.getByLabel(name, { exact: true });
let browser;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      const path = new URL(request.url()).pathname;
      assert.ok(/^\/api\/(auth\/login|properties|property-data-domains|units|persons|tenant-profiles|leases|letters)(\/|$)/.test(path), `Unexpected write: ${request.method()} ${path}`);
      writes.push({ path, body: request.postDataJSON() });
    }
  });
  await page.goto(base); await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword'); await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(base + '/objekte'); await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property); await page.getByLabel('Verwaltungsart').selectOption('MIET');
  await field(page, 'Strasse').fill('Rundbriefweg 48'); await field(page, 'PLZ').fill('12345'); await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check(); await field(page, 'Begründung der Freigabe').fill('T48 ausschließlich synthetische lokale UI-Abnahme');
  await button(page, 'Speichern').click();
  await field(page, 'Objekte suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: property, exact: true }).click(); const propertyUrl = page.url();
  for (let index = 0; index < 2; index++) {
    await page.goto(propertyUrl); await button(page, '+ Neue Einheit').click();
    const unitName = `${prefix} Wohnung ${index + 1}`;
    await field(page, 'Nummer *').fill(unitName); await field(page, 'Flaeche (m2)').fill('70'); await button(page, 'Speichern').click();
    await page.getByRole('link', { name: unitName, exact: true }).click(); const unitUrl = page.url();
    await page.goto(base + '/personen'); await button(page, '+ Neue Person').click();
    await field(page, 'Vorname').fill(`${prefix} ${index === 0 ? 'Ada' : 'Berta'}`); await field(page, 'Nachname *').fill('Synthetisch');
    await button(page, 'Speichern').click();
    await field(page, 'Personen suchen').fill(prefix); await button(page.getByRole('search'), 'Suchen').click();
    await page.getByRole('link', { name: names[index], exact: true }).click(); await button(page, 'Als Mieter kennzeichnen').click();
    await page.getByText('Mieter', { exact: true }).waitFor();
    await page.goto(unitUrl); await button(page, '+ Mietvertrag').click();
    const tenant = field(page, 'Mieter *'); await tenant.getByRole('option', { name: names[index], exact: true }).waitFor({ state: 'attached' });
    await tenant.selectOption({ label: names[index] }); await field(page, 'Vertragsbeginn *').fill('2026-01-01');
    await field(page, 'Kaltmiete (EUR) *').fill('700.00'); await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
    const created = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/leases');
    await button(page, 'Speichern').click(); const response = await created; assert.equal(response.status(), 201, await response.text());
  }
  await page.getByRole('link', { name: 'Briefe', exact: true }).click();
  await page.getByRole('heading', { name: 'Briefe', exact: true }).waitFor();
  await field(page, 'Objekt *').selectOption({ label: property });
  await button(page, 'Neue Vorlage').click(); await field(page, 'Vorlagenname *').fill(`${prefix} Hausinformation`);
  await field(page, 'Betreff *').fill('Rundschreiben {{objekt.adresse}}');
  await page.getByRole('textbox', { name: 'Brieftext *', exact: true }).fill('Hallo {{unknown}}');
  const invalid = page.waitForResponse(response => response.url().endsWith('/letters/templates') && response.request().method() === 'POST');
  await button(page, 'Vorlage speichern').click(); assert.equal((await invalid).status(), 422);
  await page.getByRole('alert').waitFor();
  if (process.env.HV_ACCEPTANCE_DEBUG) console.log(await page.locator('form').ariaSnapshot());
  await page.getByRole('textbox', { name: 'Brieftext *', exact: true }).fill('Guten Tag {{mieter.name}},\nRundschreiben zum {{datum}} für {{objekt.name}}.\nPrüftext <b>nur Klartext</b>.');
  const saved = page.waitForResponse(response => response.url().endsWith('/letters/templates') && response.request().method() === 'POST');
  await button(page, 'Vorlage speichern').click(); const templateResponse = await saved; assert.equal(templateResponse.status(), 201, await templateResponse.text());
  const template = await templateResponse.json();
  await field(page, 'Stichtag *').fill('2026-06-01'); await button(page, 'Briefe erstellen').click();
  const dialog = page.getByRole('dialog', { name: 'Briefe verbindlich erstellen?', exact: true });
  await dialog.waitFor(); assert.equal(writes.filter(write => write.path === '/api/letters/issue').length, 0);
  const issued = page.waitForResponse(response => response.url().endsWith('/letters/issue') && response.request().method() === 'POST');
  await button(dialog, 'Jetzt erstellen').click(); const issueResponse = await issued; assert.equal(issueResponse.status(), 201, await issueResponse.text());
  const documents = await issueResponse.json(); assert.equal(documents.length, 2);
  assert.deepEqual(new Set(documents.map(document => document.recipient_name)), new Set(names));
  const history = page.getByRole('region', { name: 'Briefhistorie', exact: true });
  await history.getByRole('link', { name: `PDF für ${names[1]}`, exact: true }).waitFor();
  assert.equal(await history.locator('tbody tr').count(), 2);
  async function openPdf(document) {
    const responsePromise = context.waitForEvent('response', { predicate: response => new URL(response.url()).pathname === document.pdf_url });
    const popupPromise = page.waitForEvent('popup');
    await history.getByRole('link', { name: `PDF für ${document.recipient_name}`, exact: true }).click();
    const [response, popup] = await Promise.all([responsePromise, popupPromise]);
    try {
      assert.equal(response.status(), 200);
      assert.match(response.headers()['content-type'], /application\/pdf/);
      await popup.waitForLoadState('load');
      let bytes = await response.body();
      if (!bytes.subarray(0, 4).equals(Buffer.from('%PDF'))) {
        const cdp = await context.newCDPSession(popup); let stream;
        try {
          const { frameTree } = await cdp.send('Page.getFrameTree');
          const { resource } = await cdp.send('Network.loadNetworkResource', { frameId: frameTree.frame.id, url: response.url(), options: { disableCache: false, includeCredentials: true } });
          assert.equal(resource.success, true, JSON.stringify(resource)); assert.equal(resource.httpStatusCode, 200);
          stream = resource.stream; assert.ok(stream); const chunks = []; let size = 0;
          for (;;) {
            const part = await cdp.send('IO.read', { handle: stream, size: 65536 });
            const chunk = Buffer.from(part.data, part.base64Encoded ? 'base64' : 'utf8'); chunks.push(chunk); size += chunk.length;
            assert.ok(size < 10 * 1024 * 1024); if (part.eof) break;
          }
          bytes = Buffer.concat(chunks);
        } finally { try { if (stream) await cdp.send('IO.close', { handle: stream }); } finally { await cdp.detach(); } }
      }
      assert.ok(bytes.subarray(0, 4).equals(Buffer.from('%PDF')));
      const extracted = spawnSync('pdftotext', ['-layout', '-', '-'], { input: bytes, encoding: 'utf8', timeout: 30000 });
      assert.ifError(extracted.error); assert.equal(extracted.status, 0, extracted.stderr);
      const content = extracted.stdout;
      assert.ok(content.includes(document.recipient_name)); assert.ok(content.includes('Rundbriefweg 48'));
      assert.ok(content.includes('01.06.2026')); assert.ok(content.includes('<b>nur Klartext</b>'));
      assert.ok(!content.includes('{{')); assert.ok(!content.includes(names.find(name => name !== document.recipient_name)));
      return bytes;
    } finally { await popup.close(); }
  }
  const originals = new Map();
  for (const document of documents) originals.set(document.id, await openPdf(document));
  await button(page, 'Vorlage bearbeiten').click(); await page.getByRole('textbox', { name: 'Brieftext *', exact: true }).fill('Geänderte Vorlage – gilt nur für neue Briefe.');
  const updated = page.waitForResponse(response => response.url().endsWith(`/letters/templates/${template.id}`) && response.request().method() === 'PUT');
  await button(page, 'Vorlage speichern').click(); assert.equal((await updated).status(), 200);
  await page.reload(); await field(page, 'Objekt *').selectOption({ label: property });
  await history.getByRole('link', { name: `PDF für ${names[1]}`, exact: true }).waitFor();
  assert.equal(await history.locator('tbody tr').count(), 2);
  for (const document of documents) assert.ok((await openPdf(document)).equals(originals.get(document.id)), 'Original retained byte-for-byte after template update/reload');
  await field(page, 'Vorlage *').selectOption(template.id); await field(page, 'Stichtag *').fill('2026-06-01');
  await page.setViewportSize({ width: 390, height: 844 }); await button(page, 'Briefe erstellen').click(); await dialog.waitFor(); await button(dialog, 'Abbrechen').click();
  assert.equal(writes.filter(write => write.path === '/api/letters/issue').length, 1);
  assert.deepEqual(errors, []);
  console.log('T48 UI PASS: UI-only two current tenants; persisted safe template; unsupported placeholder422; explicit confirmation; two individually addressed actual UI-opened PDFs with exact property/date/plaintext; no other recipient leakage; template edit/reload preserves exact original bytes; phone dialog. No email sent.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
