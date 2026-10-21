/* T19: genuine UI import against scripts/dev-local.sh; synthetic data only.
 * Existing Playwright via HV_PLAYWRIGHT_PACKAGE; Chrome. No API/SQL seeding,
 * screenshots or report files. Expected amounts: 700 + 150 - 200.01 = 649.99.
 */
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T19 Bankimport ${Date.now()}`;
const property = `${prefix} Objekt`;
const account = `${prefix} Giro`;
const accountIban = 'DE12500105170648489890';
const csv = Buffer.from('Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n'
  + '02.01.2026;03.01.2026;700,00;Müller;DE02120300000000202051;Kaltmiete Januar;EUR\n'
  + '04.01.2026;05.01.2026;150,00;Müller;DE02120300000000202051;NK Januar;EUR\n'
  + '06.01.2026;07.01.2026;-200,01;Heizservice;;Heizung;EUR\n');
const xml = Buffer.from('<?xml version="1.0" encoding="UTF-8"?>'
  + '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"><BkToCstmrStmt><Stmt>'
  + `<Acct><Id><IBAN>${accountIban}</IBAN></Id><Ccy>EUR</Ccy></Acct>`
  + [
    ['700.00', 'CRDT', '2026-01-02', '2026-01-03', 'Müller', 'DE02120300000000202051', 'Kaltmiete Januar'],
    ['150.00', 'CRDT', '2026-01-04', '2026-01-05', 'Müller', 'DE02120300000000202051', 'NK Januar'],
    ['200.01', 'DBIT', '2026-01-06', '2026-01-07', 'Heizservice', '', 'Heizung'],
  ].map(([amount, direction, booking, value, name, iban, purpose]) => {
    const role = direction === 'CRDT' ? 'Dbtr' : 'Cdtr';
    return `<Ntry><Amt Ccy="EUR">${amount}</Amt><CdtDbtInd>${direction}</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>`
      + `<BookgDt><Dt>${booking}</Dt></BookgDt><ValDt><Dt>${value}</Dt></ValDt><NtryDtls><TxDtls><RltdPties>`
      + `<${role}><Pty><Nm>${name}</Nm></Pty></${role}>`
      + (iban ? `<${role}Acct><Id><IBAN>${iban}</IBAN></Id></${role}Acct>` : '')
      + `</RltdPties><RmtInf><Ustrd>${purpose}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>`;
  }).join('') + '</Stmt></BkToCstmrStmt></Document>');
const zipped = spawnSync('backend/.venv/bin/python', ['-c',
  'import io,sys,zipfile; b=io.BytesIO(); z=zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED); z.writestr("statement.xml",sys.stdin.buffer.read()); z.close(); sys.stdout.buffer.write(b.getvalue())'], {input: xml});
assert.equal(zipped.status, 0, String(zipped.stderr));
let browser;
(async () => {
  browser = await chromium.launch({headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(20000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().includes('/imports') && new URL(request.url()).searchParams.get('dry_run') === 'false') writes.push(request.url());
  });
  const button = name => page.getByRole('button', {name, exact: true});
  const fill = (label, value) => page.getByLabel(label, {exact: true}).fill(value);
  const list = page.getByRole('table').filter({has: page.getByRole('columnheader', {name: 'Status', exact: true})});
  async function responseFor(dryRun, action) {
    const pending = page.waitForResponse(response => response.request().method() === 'POST'
      && response.url().includes('/imports') && new URL(response.url()).searchParams.get('dry_run') === String(dryRun));
    await action();
    return await pending;
  }
  async function choose(content = csv, name = 'synthetic.csv') {
    await page.getByLabel('Kontoauszug', {exact: true}).setInputFiles({name, mimeType: name.endsWith('.xml') ? 'application/xml' : name.endsWith('.zip') ? 'application/zip' : 'text/csv', buffer: content});
  }
  async function preview(expectedNew, expectedDuplicates) {
    const response = await responseFor(true, () => button('Vorschau erstellen').click());
    assert.equal(response.status(), 200);
    const result = await response.json();
    assert.equal(result.count, 3); assert.equal(result.new_count, expectedNew); assert.equal(result.duplicate_count, expectedDuplicates);
    assert.equal(result.total_amount, '649.99'); assert.equal(result.imported_count, 0);
    await page.getByText(`3 Umsätze: ${expectedNew} neu, ${expectedDuplicates} Dubletten.`, {exact: true}).waitFor();
    await page.getByText('Zeitraum: 02.01.2026 bis 06.01.2026', {exact: true}).waitFor();
    await page.getByText(`Gesamtbetrag: 649,99 € · Davon neu: ${expectedNew ? '649,99' : '0,00'} €`, {exact: true}).waitFor();
  }
  await page.goto(base);
  await fill('E-Mail', 'test@example.de'); await fill('Passwort', 'testpassword'); await button('Anmelden').click();
  await page.getByRole('link', {name: 'Objekte', exact: true}).waitFor();
  await page.goto(base + '/objekte'); await button('+ Neues Objekt').click();
  await fill('Name *', property); await page.getByLabel('Verwaltungsart').selectOption('WEG');
  await fill('Strasse', 'Synthetischer Importweg 19'); await fill('PLZ', '12345'); await fill('Stadt', 'Teststadt');
  await page.getByLabel('Datenbereich anlegen').check();
  await fill('Begründung der Freigabe', 'T19 synthetischer Dateiimport, keine echten Bankdaten'); await button('Speichern').click();
  await page.getByRole('link', {name: property, exact: true}).waitFor();
  await page.goto(base + '/bankkonten'); await button('+ Neues Bankkonto').click();
  await page.getByLabel('Objekt *', {exact: true}).selectOption({label: property});
  await fill('Bezeichnung *', account); await fill('IBAN', accountIban); await button('Speichern').click();
  const accountRow = page.getByRole('row').filter({hasText: account});
  await accountRow.getByRole('link', {name: 'Kontoauszug importieren', exact: true}).click();
  await page.getByRole('heading', {name: `Umsätze – ${account}`, exact: true}).waitFor();
  assert.match(page.url(), /\/bankkonten\/[^/]+\/umsaetze/);
  await page.getByText('Keine Umsätze vorhanden.', {exact: true}).waitFor();
  console.log('PASS property and bank account created entirely through UI; empty transaction list');

  await choose(Buffer.from('not a bank statement'), 'bad.csv');
  const invalid = await responseFor(true, () => button('Vorschau erstellen').click());
  assert.equal(invalid.status(), 422); await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').innerText(), /CSV|Format/);
  assert.equal(writes.length, 0);
  console.log('PASS invalid CSV gives 422 and visible UI error; no import');

  await choose(); await preview(3, 0);
  assert.equal(writes.length, 0);
  await page.getByText('Keine Umsätze vorhanden.', {exact: true}).waitFor();
  await choose(csv, 'renamed.csv');
  assert.equal(await button('Übernehmen').count(), 0);
  await preview(3, 0);
  assert.equal(writes.length, 0);
  console.log('PASS preview 3 new / 649.99 EUR / Jan 2–6 without writes; changing file invalidates confirmation');
  const imported = await responseFor(false, () => button('Übernehmen').click());
  assert.equal(imported.status(), 200); assert.equal((await imported.json()).imported_count, 3);
  await page.getByRole('status').filter({hasText: '3 neue Umsätze importiert.'}).waitFor();
  await list.locator('tbody tr').nth(2).waitFor();
  assert.equal(await list.locator('tbody tr').count(), 3);
  assert.equal(writes.length, 1);
  await page.reload(); await list.locator('tbody tr').nth(2).waitFor();
  assert.equal(await list.locator('tbody tr').count(), 3);
  for (const amount of ['700,00 €', '150,00 €', '-200,01 €']) await list.getByRole('cell', {name: amount, exact: true}).waitFor();
  console.log('PASS explicit import writes exactly 3; signed amounts persist and render after reload');

  await choose(); await preview(0, 3);
  const repeated = await responseFor(false, () => button('Übernehmen').click());
  assert.equal(repeated.status(), 200); assert.equal((await repeated.json()).imported_count, 0);
  await page.getByRole('status').filter({hasText: '0 neue Umsätze importiert.'}).waitFor();
  await list.locator('tbody tr').nth(2).waitFor(); assert.equal(await list.locator('tbody tr').count(), 3);
  console.log('PASS repeated UI preview and import: 3 duplicates, 0 new, still 3 persisted rows');
  for (const [name, content] of [['same.xml', xml], ['same.zip', zipped.stdout]]) {
    await choose(content, name); await preview(0, 3);
    const response = await responseFor(false, () => button('Übernehmen').click());
    assert.equal(response.status(), 200); assert.equal((await response.json()).imported_count, 0);
    await page.getByRole('status').filter({hasText: '0 neue Umsätze importiert.'}).waitFor();
    await list.locator('tbody tr').nth(2).waitFor(); assert.equal(await list.locator('tbody tr').count(), 3);
    console.log(`PASS ${name}: actual UI preview/import reuses CSV identities, 0 new`);
  }
  await page.getByLabel('Status', {exact: true}).selectOption('ZUGEORDNET');
  await page.getByText('Keine Umsätze vorhanden.', {exact: true}).waitFor();
  await page.getByLabel('Status', {exact: true}).selectOption('NEU');
  await list.locator('tbody tr').nth(2).waitFor(); assert.equal(await list.locator('tbody tr').count(), 3);
  assert.deepEqual(errors, []);
  console.log('PASS status filter, no page errors; no API or DB setup used');
  await button('Abmelden').click(); await button('Anmelden').waitFor();
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {if (browser) await browser.close();});
