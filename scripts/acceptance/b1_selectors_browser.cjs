/* Synthetic local selector acceptance; API18000 and Vite13000 use disposable DB55432.
 * Requires an existing Playwright/Chromium installation. Never installs or deploys.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const output = process.env.HV_ACCEPTANCE_OUTPUT || '/tmp/hv-b1-acceptance';
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE });
  const report = { pages: [], pageErrors: [], failedResponses: [], initialUnauthenticatedChecks: 0, viewports: [] };
  let authenticated = false;
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => report.pageErrors.push(error.message));
    page.on('response', response => {
      if (!authenticated && response.status() === 401 && response.url().endsWith('/api/auth/session')) report.initialUnauthenticatedChecks++;
      else if (response.status() >= 400) report.failedResponses.push([response.status(), response.url()]);
    });
    await page.goto(base + '/login');
    await page.getByLabel('E-Mail').fill('admin@alpha.de');
    await page.getByLabel('Passwort').fill('testpass1234');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    await page.waitForURL(url => !url.pathname.includes('login'));
    authenticated = true;
    await page.goto(base + '/objekte');
    await page.getByLabel('Objekte suchen').fill('Paging-selector-acceptance');
    await page.getByRole('button', { name: 'Suchen', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 50);
    await page.getByRole('button', { name: 'Weitere laden', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('tbody tr').length === 55);
    const links = await page.locator('tbody a').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
    assert.equal(new Set(links).size, 55);
    report.pages.push({ page: 'properties', firstPage: 50, totalAfterContinue: 55, unique: 55 });
    await page.screenshot({ path: path.join(output, 'properties-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('aside').getBoundingClientRect().right <= 1);
    await page.evaluate(() => window.scrollTo(0, 0));
    const widths = await page.locator('main').evaluate(node => ({ visible: node.clientWidth, content: node.scrollWidth }));
    report.viewports.push({ page: 'properties', viewport: '390x844', ...widths });
    assert.ok(widths.content <= widths.visible + 1, 'Properties page must fit mobile viewport');
    await page.screenshot({ path: path.join(output, 'properties-mobile.png'), fullPage: false });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base + '/users');
    await page.getByRole('cell', { name: 'admin@alpha.de', exact: true }).waitFor();
    report.pages.push({ page: 'users', loaded: true });
    await page.goto(base + '/personen');
    await page.getByLabel('Personen suchen').fill('not-a-real-contact');
    await page.getByRole('button', { name: 'Suchen', exact: true }).click();
    await page.getByText('Keine Personen vorhanden.', { exact: true }).waitFor();
    report.pages.push({ page: 'persons', emptySearch: true });
    await page.goto(base + '/bankkonten');
    const selector = page.getByLabel('Nach Objekt filtern', { exact: true });
    await selector.locator('option').nth(1).waitFor({ state: 'attached' });
    assert.ok(await selector.locator('option').count() <= 51);
    const chosen = await selector.locator('option').nth(1).getAttribute('value');
    await selector.selectOption(chosen);
    await page.getByLabel('Objekte durchsuchen', { exact: true }).fill('no-matching-property');
    await page.waitForFunction(() => [...document.querySelectorAll('select[aria-label="Nach Objekt filtern"] option')].length <= 2);
    assert.equal(await selector.inputValue(), chosen);
    report.pages.push({ page: 'bankAccounts', boundedSelector: true, selectedRetainedAcrossSearch: true });
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.failedResponses, []);
    report.status = 'passed';
  } finally {
    await fs.writeFile(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
    await browser.close();
  }
  console.log(JSON.stringify(report));
})().catch(error => { console.error(error); process.exitCode = 1; });
