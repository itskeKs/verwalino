#!/usr/bin/env node
/* T22: genuine UI acceptance against the isolated scripts/dev-local.sh instance.
 * Start the local UI/API (127.0.0.1:13000/18000) separately; NEVER target production.
 * Requires test@example.de / HV_TEST_PASSWORD (default testpassword), OWNER_ADMIN,
 * existing Playwright + Chrome. No API/SQL setup, routing mocks, direct payment
 * creation, Soll overrides, screenshots or output files. Synthetic records remain.
 * Run: node scripts/acceptance/t22_bank_assignment_browser.cjs
 * UI contract: Umsatz aufteilen; Zahlungsart, Mietvertrag, Betrag (EUR);
 * Zuordnung buchen. Monetary assertions use exact decimal strings/cents.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T22 ${Date.now()} ${randomUUID().slice(0, 8)}`;
const property = `${prefix} Mietobjekt`;
const account = `${prefix} Giro`;
const unit = `${prefix} Wohnung`;
const firstName = `${prefix} Ada`;
const lastName = 'Synthetisch';
const person = `${firstName} ${lastName}`;
// BankAccount has no unique IBAN constraint; each run uses its own property/account.
const accountIban = 'DE02120300000000202051';
const purposes = ['Warmmiete Januar 2026', 'Warmmiete Februar 2026', `${prefix} Nichtmiete ignorieren`];
// Generic UTF-8, semicolon-delimited source: no parser-specific hidden columns.
const csv = Buffer.from('Buchungstag;Wertstellung;Betrag;Name;Verwendungszweck\n'
  + `02.01.2026;03.01.2026;850,00;${person};${purposes[0]}\n`
  + `02.02.2026;03.02.2026;600,00;${person};${purposes[1]}\n`
  + `04.02.2026;04.02.2026;25,00;${prefix} Testdienst;${purposes[2]}\n`, 'utf8');
let browser;

function cents(value) {
  assert.equal(typeof value, 'string', 'Money must be a decimal string');
  assert.match(value, /^-?\d+\.\d{2}$/);
  return Number(value.replace('.', ''));
}
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const field = (scope, label) => scope.getByLabel(label, { exact: true });
const table = page => page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: 'Status', exact: true }) });
const transactionRow = (page, purpose) => table(page).getByRole('row').filter({ has: page.getByRole('cell', { name: purpose, exact: true }) });
async function responseFor(page, predicate, action) {
  const [response] = await Promise.all([page.waitForResponse(predicate), action()]);
  return response;
}
async function successfulJson(response, expected = 200) {
  assert.equal(response.status(), expected, `${response.request().method()} ${response.url()}: ${await response.text()}`);
  return response.json();
}
async function searchList(page, label, query) {
  await field(page, label).fill(query);
  await button(page.getByRole('search'), 'Suchen').click();
}
async function selectProperty(page, label) {
  const group = page.getByRole('group', { name: label, exact: true });
  await field(group, 'Objekte durchsuchen').fill(property);
  await group.getByRole('option', { name: property, exact: true }).waitFor({ state: 'attached' });
  await field(page, label).selectOption({ label: property });
}
function assertLines(lines, leaseId, month, amounts) {
  assert.equal(lines.length, 2);
  assert.deepEqual(lines.map(line => line.payment_type).sort(), ['KALTMIETE', 'NK_VORAUSZAHLUNG']);
  for (const line of lines) {
    assert.equal(line.lease_agreement_id, leaseId);
    assert.equal(line.period_year, 2026);
    assert.equal(line.period_month, month);
    assert.equal(line.amount, amounts[line.payment_type]);
  }
}
function assertAssignment(response, result, transaction, leaseId, accountId, propertyId, month, amounts) {
  assert.equal(result.id, transaction.id);
  assert.equal(result.status, 'ZUGEORDNET');
  const body = response.request().postDataJSON();
  assert.deepEqual(Object.keys(body), ['lines']);
  assertLines(body.lines, leaseId, month, amounts);
  const allowed = new Set(['payment_type', 'amount', 'period_year', 'period_month',
    'lease_agreement_id', 'tenant_profile_id', 'owner_profile_id', 'note']);
  for (const line of body.lines) {
    for (const key of Object.keys(line)) assert.ok(allowed.has(key), `Unexpected assignment field ${key}`);
  }
  assertLines(result.payments, leaseId, month, amounts);
  for (const payment of result.payments) {
    assert.equal(payment.bank_transaction_id, transaction.id);
    assert.equal(payment.bank_account_id, accountId);
    assert.equal(payment.property_id, propertyId);
    assert.equal(payment.direction, 'IN');
    assert.equal(payment.status, 'booked');
    assert.equal(payment.value_date, transaction.value_date);
  }
  assert.equal(result.payments.reduce((sum, payment) => sum + cents(payment.amount), 0), cents(transaction.amount));
}

(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], requests = [];
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  context.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') requests.push(request);
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  await page.goto(base);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill(process.env.HV_TEST_PASSWORD || 'testpassword');
  await button(page, 'Anmelden').click();
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();

  await page.goto(base + '/objekte');
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(property);
  const management = page.getByLabel('Verwaltungsart');
  assert.equal(await management.locator('option[value="MIET"]').count(), 1, 'MIET option required for this rent journey');
  await management.selectOption('MIET');
  await field(page, 'Strasse').fill('Synthetischer Bankweg 22');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await field(page, 'Begründung der Freigabe').fill('T22 synthetische UI-Akzeptanz im isolierten lokalen Testsystem');
  await button(page, 'Speichern').click();
  await searchList(page, 'Objekte suchen', prefix);
  await page.getByRole('link', { name: property, exact: true }).click();
  const propertyUrl = page.url();
  const propertyId = new URL(propertyUrl).pathname.split('/').pop();
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unit);
  await field(page, 'Flaeche (m2)').fill('70');
  await button(page, 'Speichern').click();
  await page.getByRole('link', { name: unit, exact: true }).click();
  const unitUrl = page.url();

  await page.goto(base + '/personen');
  await button(page, '+ Neue Person').click();
  await field(page, 'Vorname').fill(firstName);
  await field(page, 'Nachname *').fill(lastName);
  // No person IBAN: exact CSV/person name yields the MEDIUM name-only suggestion.
  await button(page, 'Speichern').click();
  await searchList(page, 'Personen suchen', prefix);
  await page.getByRole('link', { name: person, exact: true }).click();
  await button(page, 'Als Mieter kennzeichnen').click();
  await page.getByText('Mieter', { exact: true }).waitFor();
  await page.goto(unitUrl);
  await button(page, '+ Mietvertrag').click();
  await field(page, 'Mieter *').selectOption({ label: person });
  await field(page, 'Vertragsbeginn *').fill('2026-01-01');
  await field(page, 'Kaltmiete (EUR) *').fill('700.00');
  await field(page, 'NK-Vorauszahlung (EUR) *').fill('150.00');
  await button(page, 'Speichern').click();
  const leaseRow = page.getByRole('row').filter({ has: page.getByRole('cell', { name: person, exact: true }) });
  await leaseRow.getByRole('cell', { name: '700.00', exact: true }).waitFor();
  await leaseRow.getByRole('cell', { name: '150.00', exact: true }).waitFor();

  await page.goto(base + '/bankkonten');
  await button(page, '+ Neues Bankkonto').click();
  await selectProperty(page, 'Objekt *');
  await field(page, 'Bezeichnung *').fill(account);
  await field(page, 'IBAN').fill(accountIban);
  await button(page, 'Speichern').click();
  await selectProperty(page, 'Nach Objekt filtern');
  const accountRow = page.getByRole('row').filter({ hasText: account });
  await accountRow.getByRole('link', { name: 'Kontoauszug importieren', exact: true }).click();
  await page.getByRole('heading', { name: `Umsätze – ${account}`, exact: true }).waitFor();
  const transactionsUrl = page.url();
  const accountId = new URL(transactionsUrl).pathname.split('/')[2];
  console.log('PASS synthetic MIET property, unit, tenant profile, 2026 lease 700/150 and bank account created through UI');

  function isList(response) {
    if (response.request().method() !== 'GET') return false;
    const url = new URL(response.url());
    return url.pathname === '/api/bank-transactions' && url.searchParams.get('bank_account_id') === accountId
      || url.pathname === `/api/bank-accounts/${accountId}/transactions`;
  }
  async function readList(target, action) {
    const result = await successfulJson(await responseFor(target, isList, action));
    assert.ok(Array.isArray(result.items), 'Expected paginated bank transaction response');
    assert.equal(result.next_offset, null, 'Only three synthetic rows expected; pagination must not hide additional rows');
    if (result.items.length) {
      await table(target).locator('tbody tr').nth(result.items.length - 1).waitFor();
      assert.equal(await table(target).locator('tbody tr').count(), result.items.length);
    } else await target.getByText('Keine Umsätze vorhanden.', { exact: true }).waitFor();
    return result.items;
  }
  async function filterRows(status, expectedPurposes) {
    if (status === '') assert.equal(await field(page, 'Status').getByRole('option', { name: 'Alle', exact: true }).count(), 1,
      'T22 integration regression: Status must retain the T19 Alle option (value=""); do not hide processed rows behind NEU');
    const items = await readList(page, () => field(page, 'Status').selectOption(status));
    assert.deepEqual(items.map(item => item.purpose).sort(), [...expectedPurposes].sort(),
      `Status ${status || 'Alle'} must include all expected rows; a global API default of NEU must not truncate Alle`);
    for (const purpose of expectedPurposes) await transactionRow(page, purpose).waitFor();
    if (status) for (const item of items) assert.equal(item.status, status);
    return items;
  }
  async function importFile(dryRun, action) {
    const result = await successfulJson(await responseFor(page, response => response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/bank-accounts/${accountId}/imports`
      && new URL(response.url()).searchParams.get('dry_run') === String(dryRun), action));
    assert.equal(result.count, 3);
    assert.equal(result.total_amount, '1475.00');
    assert.equal(result.new_count, 3);
    assert.equal(result.duplicate_count, 0);
    assert.equal(result.imported_count, dryRun ? 0 : 3);
    return result;
  }
  await field(page, 'Kontoauszug').setInputFiles({ name: `${prefix}.csv`, mimeType: 'text/csv', buffer: csv });
  await importFile(true, () => button(page, 'Vorschau erstellen').click());
  await page.getByText('3 Umsätze: 3 neu, 0 Dubletten.', { exact: true }).waitFor();
  await page.getByText('Gesamtbetrag: 1.475,00 € · Davon neu: 1.475,00 €', { exact: true }).waitFor();
  await page.getByText('Keine Umsätze vorhanden.', { exact: true }).waitFor();
  await importFile(false, () => button(page, 'Übernehmen').click());
  await page.getByRole('status').filter({ hasText: '3 neue Umsätze importiert.' }).waitFor();
  // Reload is a real UI read; no direct GET is used to paper over filters.
  const pending = await readList(page, () => page.reload());
  assert.deepEqual(pending.map(item => item.purpose).sort(), [...purposes].sort());
  for (const item of pending) assert.equal(item.status, 'NEU');
  const warm = pending.find(item => item.purpose === purposes[0]);
  const partial = pending.find(item => item.purpose === purposes[1]);
  const ignored = pending.find(item => item.purpose === purposes[2]);
  assert.equal(warm.amount, '850.00'); assert.equal(partial.amount, '600.00'); assert.equal(ignored.amount, '25.00');
  assert.equal(warm.suggestions.length, 1);
  const proposal = warm.suggestions[0];
  assert.equal(proposal.confidence, 'MEDIUM'); assert.equal(proposal.requires_review, false);
  assert.equal(proposal.unallocated_amount, '0.00');
  const leaseId = proposal.lines[0].lease_agreement_id;
  assert.ok(leaseId);
  assertLines(proposal.lines, leaseId, 1, { KALTMIETE: '700.00', NK_VORAUSZAHLUNG: '150.00' });
  assert.equal(partial.suggestions.length, 1);
  assert.equal(partial.suggestions[0].requires_review, true);
  assert.equal(partial.suggestions[0].unallocated_amount, '600.00');
  assert.equal(partial.suggestions[0].lines.length, 2);
  for (const line of partial.suggestions[0].lines) assert.equal(line.amount, null);
  console.log('PASS CSV preview/import 1475.00 EUR, persisted rows, safe MEDIUM warm suggestion and unresolved 600 split');

  const stale = await context.newPage(); stale.setDefaultTimeout(20000);
  const staleRows = await readList(stale, () => stale.goto(transactionsUrl));
  assert.ok(staleRows.some(item => item.id === warm.id && item.status === 'NEU'));
  await transactionRow(stale, purposes[0]).getByRole('button', { name: 'Vorschlag übernehmen', exact: true }).waitFor();
  const assignResponse = await responseFor(page, response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/bank-transactions/${warm.id}/assign`,
  () => button(transactionRow(page, purposes[0]), 'Vorschlag übernehmen').click());
  const assignedWarm = await successfulJson(assignResponse);
  assertAssignment(assignResponse, assignedWarm, warm, leaseId, accountId, propertyId, 1,
    { KALTMIETE: '700.00', NK_VORAUSZAHLUNG: '150.00' });
  const conflict = await responseFor(stale, response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/bank-transactions/${warm.id}/assign`,
  () => button(transactionRow(stale, purposes[0]), 'Vorschlag übernehmen').click());
  assert.equal(conflict.status(), 409);
  await stale.getByRole('alert').filter({ hasText: /already|bereits|verarbeitet|zugeordnet/i }).waitFor();
  await stale.close();
  console.log('PASS Warmmiete books exactly two linked payments; stale second-tab proposal rejected with visible 409');

  async function statement(month, expectedIst, expectedBalance) {
    await page.goto(base + '/mietkonto');
    const lease = field(page, 'Mietvertrag *');
    await lease.locator(`option[value="${leaseId}"]`).waitFor({ state: 'attached' });
    await lease.selectOption(leaseId);
    await field(page, 'Zeitraum von *').fill(`2026-${month || '01'}-01`);
    await field(page, 'Zeitraum bis *').fill(month === '01' ? '2026-01-31' : month === '02' ? '2026-02-28' : '2026-12-31');
    const result = await successfulJson(await responseFor(page, response => response.request().method() === 'GET'
      && new URL(response.url()).pathname === `/api/payments/mietkonto/lease/${leaseId}`,
    () => button(page, 'Abrufen').click()));
    assert.equal(result.soll_total, month ? '850.00' : '10200.00');
    assert.equal(result.ist_total, expectedIst); assert.equal(result.balance, expectedBalance);
    const summary = page.getByRole('table').filter({ has: page.getByRole('cell', { name: 'Ist gesamt', exact: true }) });
    await summary.getByRole('row').filter({ hasText: 'Ist gesamt' }).getByRole('cell', { name: `${expectedIst} EUR`, exact: true }).waitFor();
    if (month) {
      const detail = page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: 'Offen', exact: true }) });
      for (const [type, label, soll, ist, open] of month === '01'
        ? [['KALTMIETE', 'Kaltmiete', '700.00', '700.00', '0.00'], ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', '150.00', '150.00', '0.00']]
        : [['KALTMIETE', 'Kaltmiete', '700.00', '500.00', '200.00'], ['NK_VORAUSZAHLUNG', 'NK-Vorauszahlung', '150.00', '100.00', '50.00']]) {
        const item = result.open_items.find(value => value.period_year === 2026 && value.period_month === Number(month) && value.payment_type === type);
        assert.ok(item); assert.equal(item.soll, soll); assert.equal(item.ist, ist); assert.equal(item.open_amount, open);
        const row = detail.getByRole('row').filter({ has: page.getByRole('cell', { name: label, exact: true }) });
        await row.waitFor();
        assert.deepEqual(await row.getByRole('cell').allTextContents(), [`2026/${month}`, label, `${soll} EUR`, `${ist} EUR`, `${open} EUR`]);
      }
    }
  }
  await statement('01', '850.00', '0.00');
  await page.reload();
  await statement('01', '850.00', '0.00');
  console.log('PASS January Mietkonto after reload: Kalt 700 and NK 150 paid, open 0; no duplicate payment');

  await readList(page, () => page.goto(transactionsUrl));
  await button(transactionRow(page, purposes[1]), 'Aufteilen').click();
  const dialog = page.getByRole('dialog', { name: 'Umsatz aufteilen', exact: true });
  await dialog.waitFor();
  const amounts = field(dialog, 'Betrag (EUR)');
  assert.equal(await amounts.count(), 2);
  await field(dialog, 'Mietvertrag').nth(0).locator(`option[value="${leaseId}"]`).waitFor({ state: 'attached' });
  await button(dialog, 'Zuordnung buchen').waitFor({ state: 'visible' });
  for (const [index, type] of ['KALTMIETE', 'NK_VORAUSZAHLUNG'].entries()) {
    assert.equal(await field(dialog, 'Zahlungsart').nth(index).inputValue(), type);
    assert.equal(await field(dialog, 'Mietvertrag').nth(index).inputValue(), leaseId);
    assert.equal(await field(dialog, 'Periode Jahr').nth(index).inputValue(), '2026');
    assert.equal(await field(dialog, 'Periode Monat').nth(index).inputValue(), '2');
  }
  const assignmentPosts = () => requests.filter(request => /\/api\/bank-transactions\/[^/]+\/assign$/.test(new URL(request.url()).pathname)).length;
  const beforeInvalid = assignmentPosts();
  async function rejectInvalid() {
    const submit = button(dialog, 'Zuordnung buchen');
    if (await submit.isEnabled()) {
      await submit.click();
      await dialog.getByRole('alert').waitFor();
    }
    assert.equal(assignmentPosts(), beforeInvalid, 'Invalid split must never send a booking request');
  }
  await amounts.nth(0).fill(''); await amounts.nth(1).fill(''); await rejectInvalid();
  await amounts.nth(0).fill('500.01'); await amounts.nth(1).fill('100.00'); await rejectInvalid();
  await amounts.nth(0).fill('500.00');
  // Retain the real proposal targets/periods; never synthesize an API assignment.
  const partialResponse = await responseFor(page, response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/bank-transactions/${partial.id}/assign`,
  () => button(dialog, 'Zuordnung buchen').click());
  const assignedPartial = await successfulJson(partialResponse);
  assertAssignment(partialResponse, assignedPartial, partial, leaseId, accountId, propertyId, 2,
    { KALTMIETE: '500.00', NK_VORAUSZAHLUNG: '100.00' });
  await dialog.waitFor({ state: 'hidden' });
  const ignoreResponse = await responseFor(page, response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/bank-transactions/${ignored.id}/ignore`,
  () => button(transactionRow(page, purposes[2]), 'Ignorieren').click());
  const ignoredResult = await successfulJson(ignoreResponse);
  assert.equal(ignoredResult.id, ignored.id); assert.equal(ignoredResult.status, 'IGNORIERT');
  assert.ok(!ignoredResult.payments || ignoredResult.payments.length === 0);
  console.log('PASS invalid/absent split amounts do not POST; explicit February 500/100 booked; 25 ignored without payment result');

  await readList(page, () => page.reload());
  await filterRows('ZUGEORDNET', purposes.slice(0, 2));
  for (const purpose of purposes.slice(0, 2)) {
    const row = transactionRow(page, purpose);
    assert.equal(await button(row, 'Vorschlag übernehmen').count(), 0);
    assert.equal(await button(row, 'Aufteilen').count(), 0);
    assert.equal(await button(row, 'Ignorieren').count(), 0);
  }
  await filterRows('IGNORIERT', [purposes[2]]);
  await filterRows('', purposes);
  await filterRows('NEU', []);
  console.log('PASS reloaded filters: Zugeordnet 2, Ignoriert 1, Alle 3, Neu 0; pagination preserved');
  await statement('02', '600.00', '250.00');
  await statement('', '1450.00', '8750.00');
  await statement('01', '850.00', '0.00');
  assert.equal(assignedWarm.payments.length + assignedPartial.payments.length, 4);
  assert.equal(requests.filter(request => /\/api\/payments(?:\/|$)/.test(new URL(request.url()).pathname)).length, 0,
    'No direct payment/booking/Soll override request allowed');
  assert.equal(assignmentPosts(), 3, 'Only warm + stale warm + explicit partial assignment allowed');
  assert.deepEqual(errors, []);
  console.log('PASS persisted rent ledger: January 850 paid/0 open; February 600 paid/250 open; year Soll 10200, Ist 1450, balance 8750; ignored 25 creates no rent payment');
  await button(page, 'Abmelden').click();
  await button(page, 'Anmelden').waitFor();
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
