/* T13: real UI journey against scripts/dev-local.sh. Creates synthetic data only.
 * Requires existing Playwright + Chrome; no seed API, SQL, or report files.
 * HV_PLAYWRIGHT_PACKAGE=/path/to/playwright node scripts/acceptance/t13_weg_year_browser.cjs
 */
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T13 WEG-Jahr 2025 ${Date.now()}`;
const property = `${prefix} Testobjekt`;
const ada = `${prefix} Ada Sechzig`;
const bruno = `${prefix} Bruno Vierzig`;
const account = `${prefix} Giro`;
const wording = `${prefix}: Die Nachschüsse und Anpassungen der Vorschüsse für 2025 werden beschlossen. Abrechnungsspitze Ada -600 EUR, Bruno -400 EUR; Summe -1000 EUR.`;
const browserErrors = [];
const failedResponses = [];
const anonymousSessionProbes = [];
let authenticated = false;
let browser, page;

async function go(path) { await page.goto(base + path); }
async function click(name) { await page.getByRole('button', { name, exact: true }).click(); }
async function fill(label, value) { await page.getByLabel(label, { exact: true }).fill(value); }
async function select(label, value) { await page.getByLabel(label).selectOption(value); }
async function coverage(reason) {
  await fill('Grundlage der Prüfung', reason);
  await page.getByLabel('Alle angezeigten Beträge').check();
  await click('Datengrundlage speichern');
  await page.getByText('Prüfung gespeichert.', { exact: false }).waitFor();
}
async function pdf(button, expectedText) {
  const response = context.waitForEvent('response', { predicate: r => r.url().startsWith(base + '/api/') && r.url().endsWith('/pdf') });
  const opened = context.waitForEvent('page');
  await click(button);
  const res = await response;
  assert.equal(res.status(), 200, `Immediate UI PDF: ${res.url()}`);
  assert.match(res.headers()['content-type'], /application\/pdf/);
  const tab = await opened;
  await tab.waitForLoadState('load');
  // Supplementary HTTP/text inspection of the PDF already opened through UI.
  const downloaded = await context.request.get(res.url().replace(':13000', ':18000'));
  assert.equal(downloaded.status(), 200);
  const bytes = await downloaded.body();
  assert.ok(bytes.subarray(0, 5).equals(Buffer.from('%PDF-')));
  const extracted = spawnSync('pdftotext', ['-raw', '-', '-'], { input: bytes, encoding: 'utf8' });
  assert.equal(extracted.status, 0, extracted.stderr);
  // PDF layout may wrap a field across lines without changing its content.
  const textContent = extracted.stdout.replace(/\s+/g, ' ');
  for (const text of expectedText) assert.ok(textContent.includes(text), `PDF missing ${text}: ${res.url()}\n${extracted.stdout}`);
  console.log('PDF 200 + text verified:', res.url());
  await tab.close();
}
let context;
(async () => {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('dialog', d => d.accept());
  context.on('page', p => p.on('pageerror', e => browserErrors.push(e.message)));
  page.on('pageerror', e => browserErrors.push(e.message));
  context.on('response', r => {
    if (!r.url().startsWith(base + '/api/') || r.status() < 400) return;
    if (!authenticated && r.url() === base + '/api/auth/session' && r.status() === 401) {
      anonymousSessionProbes.push(r.status());
    } else {
      failedResponses.push(`${r.status()} ${r.url()}`);
    }
  });
  await go('/'); await fill('E-Mail', 'test@example.de'); await fill('Passwort', 'testpassword'); await click('Anmelden');
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  authenticated = true;
  assert.ok(anonymousSessionProbes.length > 0, 'Anonymous bootstrap must require authentication');
  console.log('Expected pre-login session probes:', anonymousSessionProbes);

  await go('/objekte'); await click('+ Neues Objekt'); await fill('Name *', property);
  await select('Verwaltungsart', 'WEG'); await fill('Strasse', 'Synthetischer WEG-Weg 13'); await fill('PLZ', '12345'); await fill('Stadt', 'Teststadt');
  await page.getByLabel('Datenbereich anlegen').check(); await fill('Begründung der Freigabe', 'T13 synthetischer WEG-Jahresdurchstich'); await click('Speichern');
  await page.getByRole('link', { name: property, exact: true }).click();
  await page.getByLabel('Nenner *').fill('1000'); await click('MEA-Skala bestätigen');
  await click('+ Neue Einheit');
  for (const [unit, area, mea] of [['T13-A', '60', '600'], ['T13-B', '40', '400']]) {
    if (unit === 'T13-B') await click('+ Neue Einheit');
    await fill('Nummer *', unit); await fill('Flaeche (m2)', area); await fill('MEA (Miteigentumsanteil)', mea); await click('Speichern');
    await page.getByRole('link', { name: unit, exact: true }).waitFor();
    assert.match(await page.getByRole('row').filter({ has: page.getByRole('link', { name: unit, exact: true }) }).innerText(), new RegExp(`${mea}\\.000000 / 1\\.000`));
  }
  const propertyUrl = page.url(); const propertyId = propertyUrl.split('/').pop();
  for (const [first, last] of [[`${prefix} Ada`, 'Sechzig'], [`${prefix} Bruno`, 'Vierzig']]) {
    await go('/personen'); await click('+ Neue Person'); await fill('Vorname', first); await fill('Nachname *', last); await click('Speichern');
    await page.getByRole('link', { name: `${first} ${last}`, exact: true }).click(); await click('Als Eigentuemer kennzeichnen'); await page.getByText('Eigentuemer', { exact: true }).waitFor();
  }
  for (const [unit, owner, mea] of [['T13-A', ada, '600'], ['T13-B', bruno, '400']]) {
    await page.goto(propertyUrl); await page.getByRole('link', { name: unit, exact: true }).click(); await click('+ Eigentum');
    await select('Eigentuemer *', { label: owner }); await fill('Gueltig ab *', '2025-01-01'); await fill('MEA-Anteil', mea); await click('Speichern');
    await page.getByRole('cell', { name: owner, exact: true }).waitFor();
  }
  console.log('UI property, confirmed 600/1000 + 400/1000 MEA, two owners and ownerships created');

  await go('/wirtschaftsplaene'); await click('+ Neuer Wirtschaftsplan'); await select('WEG-Objekt *', propertyId);
  await fill('Wirtschaftsjahr *', '2025'); await fill('Zeitraum von *', '2025-01-01'); await fill('Zeitraum bis *', '2025-12-31'); await fill('Rücklagenzuführung (Jahr)', '1000.00'); await click('Anlegen');
  await click('+ Position'); await page.locator('tbody select').selectOption('WEG_VERWALTERKOSTEN'); await page.locator('tbody input').fill('5000.00');
  await fill('Rücklagenzuführung (Jahr)', '1000.00');
  await coverage('T13 vollständig: 5000 EUR Plan-Kosten und 1000 EUR Rücklage, MEA 600/400, volles Jahr 2025.'); await click('Berechnen');
  const advances = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Hausgeld-Vorschüsse je Eigentümer', exact: true }) });
  await advances.waitFor();
  for (const [owner, annual, monthly] of [[ada, '3600.00', '300.00'], [bruno, '2400.00', '200.00']]) {
    const row = advances.getByRole('row').filter({ hasText: owner });
    assert.ok((await row.innerText()).includes(`${annual} EUR`)); assert.ok((await row.innerText()).includes(`${monthly} EUR`));
  }
  await click('Finalisieren'); await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).waitFor();
  const planId = page.url().split('/').pop();
  await pdf('PDF herunterladen', ['Wirtschaftsplan', prefix, '3.600,00', '2.400,00']);
  console.log('UI plan total 6000, Ada 3600/300, Bruno 2400/200 verified');

  await go('/bankkonten'); await click('+ Neues Bankkonto'); await select('Objekt *', propertyId); await fill('Bezeichnung *', account); await click('Speichern'); await page.getByText(account, { exact: true }).waitFor();
  for (const [owner, amount, months] of [[ada, '300.00', 12], [bruno, '200.00', 11]]) {
    for (let m = 1; m <= months; m++) {
      await go('/zahlungen'); await click('+ Neue Zahlung'); await select('Bankkonto *', { label: `${account} (${property})` }); await select('Zahlungstyp *', 'HAUSGELD_VORSCHUSS');
      await fill('Betrag *', amount); await fill('Wertstellungsdatum *', `2025-${String(m).padStart(2, '0')}-03`); await select('Eigentuemer', { label: owner }); await fill('Periode Jahr', '2025'); await fill('Periode Monat', String(m)); await fill('Notiz', `${prefix} Hausgeld ${m}/2025`);
      await click('Anlegen'); await click('Buchen'); await page.getByRole('heading', { name: 'Zahlung Gebucht', exact: true }).waitFor();
    }
    console.log('UI booked monthly Hausgeld:', owner, months, amount);
  }
  for (const [category, label, amount] of [['WEG_VERWALTERKOSTEN', 'WEG-Verwalterkosten', '3000.00'], ['GARTENPFLEGE', 'Gartenpflege', '2000.00'], ['ERHALTUNGSRUECKLAGE', 'Erhaltungsruecklage / Instandhaltungsruecklage', '1000.00']]) {
    await go('/kosten'); await click('+ Neue Kostenposition'); await select('Objekt *', propertyId); await select('Kategorie *', { label: `${category} ${label}` });
    await fill('Rechnungsdatum *', '2025-12-15'); await fill('Leistungszeitraum von *', '2025-01-01'); await fill('Leistungszeitraum bis *', '2025-12-31'); await fill('Bruttobetrag *', amount); await fill('Belegnummer', `${prefix}-${category}`); await select('Verteilerschl. Eigentuemer', 'MEA'); await click('Speichern');
    await page.getByRole('row').filter({ hasText: `${category} ` }).filter({ hasText: property }).getByRole('link', { name: 'Details', exact: true }).click(); await click('Bestaetigen'); await page.getByText('Bestaetigt', { exact: true }).first().waitFor();
  }
  console.log('UI confirmed costs 3000 + 2000 and separate reserve 1000');

  await go('/weg-abrechnungen'); await click('+ Neue WEG-Abrechnung'); await select('WEG-Objekt *', propertyId); await select('Wirtschaftsplan (Soll) *', planId); await fill('Zeitraum von *', '2025-01-01'); await fill('Zeitraum bis *', '2025-12-31'); await click('Anlegen');
  const derived = page.waitForResponse(r => r.url().endsWith('/paid-advances-derived'));
  await click('Aus Zahlenstand übernehmen');
  assert.equal((await derived).status(), 200);
  await page.waitForFunction(() => [...document.querySelectorAll('input')].some(e => e.value === '3600.00'));
  assert.equal(await page.getByLabel(`Tatsächlich gezahltes Hausgeld · ${ada}`).inputValue(), '3600.00');
  assert.equal(await page.getByLabel(`Tatsächlich gezahltes Hausgeld · ${bruno}`).inputValue(), '2200.00');
  await fill('Rücklagen-Anfangsbestand', '0.00'); await fill('Rücklagen-Entnahmen (Jahr)', '0.00');
  await coverage('T13 vollständig: 5800 Ist-Hausgelder, 6000 Soll, 5000 Kosten, 1000 Rücklage; Anfang und Entnahmen bestätigt 0.'); await click('Berechnen');
  for (const [owner, garden, admin, cost, plan, paid, balance, reserve] of [[ada, '1200.00', '1800.00', '3000.00', '3600.00', '3600.00', '-600.00', '600.00'], [bruno, '800.00', '1200.00', '2000.00', '2400.00', '2200.00', '-400.00', '400.00']]) {
    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: owner, exact: true }) }); await card.waitFor();
    for (const [label, expected] of [['Gartenpflege', garden], ['WEG-Verwalterkosten', admin], ['Summe Ist-Kosten', cost], ['Soll-Vorschüsse', plan], ['geleistete Vorschuesse', paid], ['Abrechnungsspitze', balance], ['Vermoegensbildung', reserve]]) {
      const row = card.getByRole('row').filter({ hasText: label }); assert.ok((await row.innerText()).endsWith(`${expected} EUR`), `${owner}: ${label} ${expected}`);
    }
  }
  const overview = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Übersicht', exact: true }) });
  assert.ok((await overview.innerText()).includes('5000.00 EUR')); assert.ok((await overview.innerText()).includes('-1000.00 EUR'));
  await click('Finalisieren'); await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).waitFor();
  await pdf('PDF herunterladen', ['Jahresabrechnung', prefix, '3.600,00', '2.200,00', '-600,00', '-400,00']);
  await page.reload(); await page.getByRole('heading', { name: 'WEG-Jahresabrechnung Finalisiert', exact: true }).waitFor();
  console.log('UI settlement: Ada cost/plan/paid/spitze 3000/3600/3600/-600; Bruno 2000/2400/2200/-400; totals 5000/6000/5800/-1000');

  await go('/vermoegensberichte'); await click('+ Neuer Vermögensbericht'); await select('WEG-Objekt *', propertyId); await fill('Zeitraum von *', '2025-01-01'); await fill('Stichtag (bis) *', '2025-12-31'); await click('Anlegen');
  await fill('Rücklage Anfangsbestand', '0.00'); await fill('Rücklage Zuführung', '1000.00'); await fill('Rücklage Entnahmen', '0.00'); await fill(`Kontostand · ${account}`, '800.00');
  await page.getByRole('button', { name: '+ Position', exact: true }).first().click(); await page.getByPlaceholder('Bezeichnung', { exact: true }).fill('T13 Hausgeldrückstand Bruno Dezember 2025'); await page.getByPlaceholder('0.00', { exact: true }).last().fill('200.00');
  await coverage('T13 Stichtag vollständig: Bank 5800 Einnahmen minus 5000 bezahlte Kosten = 800; Forderung Bruno 200; keine Verbindlichkeiten; Rücklage 0+1000-0=1000.'); await click('Berechnen');
  const assets = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Vermögensaufstellung', exact: true }) }); await assets.waitFor();
  for (const [label, amount] of [['Endbestand', '1000.00'], ['Bankkonto', '800.00'], ['Forderung', '200.00']]) assert.ok((await assets.getByRole('row').filter({ hasText: label }).innerText()).endsWith(`${amount} EUR`));
  await click('Finalisieren'); await page.getByRole('button', { name: 'PDF herunterladen', exact: true }).waitFor();
  await pdf('PDF herunterladen', ['Verm', prefix, '1.000,00', '800,00', '200,00']);
  console.log('UI assets verified: reserve 1000, bank 800, receivable 200, no payables');

  await go('/versammlungen'); await click('+ Neue Versammlung'); await select('WEG-Objekt *', propertyId); await fill('Datum *', '2026-01-15'); await fill('Ort', 'T13 synthetischer Versammlungsraum'); await fill('Einladungstext', `${prefix}: Jahresabrechnung 2025 und Vermögensbericht`); await click('Versammlung anlegen');
  await click('+ TOP hinzufuegen'); await fill('Titel *', 'T13 Jahresabrechnung 2025'); await fill('Beschreibung', 'Kosten und Abrechnungsspitzen beschlossen'); await click('TOP anlegen');
  await page.getByText('TOP 1:', { exact: true }).waitFor(); assert.ok((await page.getByText('TOP 1:', { exact: true }).locator('..').innerText()).includes('T13 Jahresabrechnung 2025')); await click('+ Beschluss'); await fill('Beschlusswortlaut *', wording); await fill('Stimmen Ja *', '2'); await click('MEA-Stimmen anzeigen (optional)'); await fill('MEA Ja', '1000'); await fill('MEA Nein', '0'); await fill('MEA Enthaltung', '0'); await click('Beschluss erfassen'); await page.getByText(wording, { exact: true }).waitFor();
  const protocol = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Protokoll (Niederschrift)', exact: true }) });
  await protocol.getByRole('button', { name: 'Bearbeiten', exact: true }).click();
  await protocol.locator('textarea').fill(`${prefix}: Beide Eigentümer anwesend. 2 Ja-Stimmen, 0 Nein, 0 Enthaltungen, 1000/1000 MEA. Beschluss angenommen.`);
  await protocol.getByRole('button', { name: 'Speichern', exact: true }).click();
  await protocol.getByText('Beide Eigentümer anwesend.', { exact: false }).waitFor();
  await click('Als durchgefuehrt markieren'); await page.getByRole('button', { name: 'In Beschluss-Sammlung', exact: true }).waitFor(); await click('In Beschluss-Sammlung'); await page.getByRole('button', { name: 'Uebernommen', exact: true }).waitFor();
  await pdf('Niederschrift-PDF', [prefix, 'T13 Jahresabrechnung 2025', 'Angenommen', '1000/1000 MEA']);
  await go('/beschluss-sammlung'); await page.locator('main select').selectOption(propertyId); await page.getByRole('cell', { name: wording, exact: true }).waitFor();
  const entry = page.getByRole('row').filter({ hasText: wording }); assert.equal(await entry.getByRole('cell').first().innerText(), '1');
  await pdf('Beschluss-Sammlung PDF', [prefix, '2025', 'T13 synthetischer Versammlungsraum']);
  assert.deepEqual(browserErrors, []); assert.deepEqual(failedResponses, []);
  console.log('PASS: full T13 UI journey, all 5 offered PDFs immediately 200 with checked content, no failed API responses or page errors.', property);
})().catch(async error => { console.error(error); if (page) { console.error('At', page.url()); console.error(await page.locator('main').innerText().catch(() => '')); } process.exitCode = 1; }).finally(async () => { if (browser) await browser.close(); });
