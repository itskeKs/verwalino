/* T14: real password dialog against scripts/dev-local.sh, synthetic admin only.
 * HV_PLAYWRIGHT_PACKAGE=/existing/playwright node scripts/acceptance/t14_password_browser.cjs
 * No API seeding, SQL, screenshots or result files. Restores the starting password.
 */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const original = process.env.HV_TEST_PASSWORD || 'testpassword';
const replacement = 'T14BrowserPassword456!';
let browser;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const button = name => page.getByRole('button', { name, exact: true });
  async function login(password, expected) {
    await page.getByLabel('E-Mail', { exact: true }).fill('test@example.de');
    await page.getByLabel('Passwort', { exact: true }).fill(password);
    const response = page.waitForResponse(r => r.url().endsWith('/api/auth/login') && r.request().method() === 'POST');
    await button('Anmelden').click();
    assert.equal((await response).status(), expected);
    if (expected === 200) await button('Abmelden').waitFor();
    else await page.getByText('Invalid credentials', { exact: true }).waitFor();
  }
  async function fillPasswords(oldPassword, newPassword, confirmation = newPassword) {
    await page.getByLabel('Aktuelles Passwort', { exact: true }).fill(oldPassword);
    await page.getByLabel('Neues Passwort', { exact: true }).fill(newPassword);
    await page.getByLabel('Neues Passwort bestätigen', { exact: true }).fill(confirmation);
  }
  async function submit(expected) {
    const response = page.waitForResponse(r => r.url().endsWith('/api/auth/password') && r.request().method() === 'POST');
    await page.getByRole('dialog').getByRole('button', { name: 'Passwort ändern', exact: true }).click();
    assert.equal((await response).status(), expected);
  }
  await page.goto(base);
  await login(original, 200);
  await button('Passwort ändern').click();
  await fillPasswords(original, replacement, 'different');
  await page.getByRole('dialog').getByRole('button', { name: 'Passwort ändern', exact: true }).click();
  await page.getByText('Neue Passwörter stimmen nicht überein.', { exact: true }).waitFor();
  console.log('PASS confirmation mismatch visible');
  await fillPasswords('wrong-password', replacement);
  await submit(401);
  await page.getByText('Altes Passwort ist falsch.', { exact: true }).waitFor();
  console.log('PASS wrong old password: 401 + UI message');
  await fillPasswords(original, 'short');
  await submit(422);
  await page.getByText('Neues Passwort erfüllt die Anforderungen nicht.', { exact: true }).waitFor();
  console.log('PASS weak password: 422 + UI message');
  await fillPasswords(original, replacement);
  await submit(204);
  await button('Anmelden').waitFor();
  console.log('PASS dialog change 204 + logout');
  await login(original, 401);
  await login(replacement, 200);
  await page.reload();
  await button('Abmelden').waitFor();
  console.log('PASS old password rejected, new login and persisted browser session');
  await button('Passwort ändern').click();
  await fillPasswords(replacement, original);
  await submit(204);
  await button('Anmelden').waitFor();
  await login(original, 200);
  await button('Abmelden').click();
  await button('Anmelden').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS original password restored through dialog; no page errors');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
