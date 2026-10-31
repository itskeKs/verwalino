#!/usr/bin/env node
/* T47: UI-only setup and actual loopback SMTP. Never forwards mail.
 * Start scripts/dev-local.sh with HV_SMTP_HOST=127.0.0.1 HV_SMTP_PORT=11025
 * HV_SMTP_FROM=verwaltung@example.invalid HV_SMTP_SECURITY=plain and /tmp storage.
 * Requires Chrome, Playwright, uv and pdftotext. No fixtures or API mutations.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T47 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} MIET`;
const unitName = `${prefix} Wohnung`;
const scopeName = `${prefix} Mietkreis`;
const firstName = `${prefix} Ada`;
const lastName = 'Zinsmieter';
const personName = `${firstName} ${lastName}`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
// Decimal strings only; no Number/float conversion, rounding or locale parsing.
function decimal(value) {
  assert.equal(typeof value, 'string', 'DTO decimal must be a string');
  assert.match(value, /^-?\d+(?:\.\d+)?$/);
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  const integer = whole.replace(/^0+(?=\d)/, '');
  const tail = fraction.replace(/0+$/, '');
  const token = integer + (tail ? `.${tail}` : '');
  return negative && token !== '0' ? `-${token}` : token;
}
const equalDecimal = (actual, expected) => assert.equal(decimal(actual), decimal(expected));
async function eventually(check) {
  const end = Date.now() + 20000;
  for (;;) {
    try { await check(); return; }
    catch (error) {
      if (!(error instanceof assert.AssertionError) || Date.now() >= end) throw error;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
}
async function successfulJson(response, status = 200) {
  if (response.status() !== status) {
    assert.fail(`${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}
async function selectLabel(page, name, label) {
  const select = page.getByRole('combobox', { name, exact: true });
  await select.getByRole('option', { name: label, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label });
}
async function selectId(page, name, id) {
  const select = page.getByRole('combobox', { name, exact: true });
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${name}: missing observed ID ${id}`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
}
// Only responses to requests started after the new main document committed.
async function freshReload(page, predicates) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    const pending = predicates.map(predicate => page.waitForResponse(response =>
      requests.has(response.request()) && predicate(response)));
    const result = await Promise.all([...pending, page.reload()]);
    return await Promise.all(result.slice(0, predicates.length).map(response => successfulJson(response)));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
  }
}
function interestSnapshot(value) {
  assert.ok(value && typeof value === 'object');
  return { ...value, base_interest_rate: decimal(value.base_interest_rate),
    annual_rate: decimal(value.annual_rate), items: value.items.map(item => ({ ...item,
      principal: decimal(item.principal), interest: decimal(item.interest) })) };
}
const net = require('node:net');
const smtpMessages = [], smtpSockets = new Set();
const smtpServer = net.createServer(socket => {
  smtpSockets.add(socket); socket.on('close', () => smtpSockets.delete(socket));
  socket.write('220 localhost T47 test-only\r\n');
  let buffer = '', data = false, lines = [], sender = '', recipients = [];
  socket.on('data', chunk => {
    buffer += chunk.toString('latin1');
    while (buffer.includes('\r\n')) {
      const pos = buffer.indexOf('\r\n'), line = buffer.slice(0, pos); buffer = buffer.slice(pos + 2);
      if (data) {
        if (line === '.') {
          smtpMessages.push({ sender, recipients: [...recipients], raw: Buffer.from(lines.join('\r\n') + '\r\n', 'latin1') });
          data = false; lines = []; socket.write('250 2.0.0 accepted locally, not forwarded\r\n');
        } else lines.push(line.startsWith('..') ? line.slice(1) : line);
      } else if (/^(EHLO|HELO) /i.test(line)) socket.write('250 localhost\r\n');
      else if (/^MAIL FROM:/i.test(line)) { sender = line.slice(10).trim(); recipients = []; socket.write('250 OK\r\n'); }
      else if (/^RCPT TO:/i.test(line)) {
        const recipient = line.slice(8).trim();
        if (recipient.toLowerCase() !== '<tenant@example.invalid>') socket.write('550 Only synthetic recipient allowed\r\n');
        else { recipients.push(recipient); socket.write('250 OK\r\n'); }
      } else if (line.toUpperCase() === 'DATA') { data = true; socket.write('354 End with dot\r\n'); }
      else if (line.toUpperCase() === 'QUIT') { socket.end('221 Bye\r\n'); }
      else if (line.toUpperCase() === 'RSET') { sender = ''; recipients = []; socket.write('250 OK\r\n'); }
      else socket.write('502 Unsupported command\r\n');
    }
  });
});
let browser;
(async () => {
  await new Promise((resolve, reject) => { smtpServer.once('error', reject); smtpServer.listen(11025, '127.0.0.1', resolve); });
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const errors = [], actualWrites = [], allowedWrites = [], authResponses = [], authConsole = [];
  let authPhase = 'anonymous';
  let pendingWrite = null;
  const sessions = new Set([`${base}/api/auth/session`, 'http://127.0.0.1:18000/api/auth/session']);
  context.on('page', tab => {
    tab.setDefaultTimeout(20000);
    tab.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
    tab.on('console', message => {
      if (message.type() !== 'error') return;
      const url = message.location().url;
      if (/status of 401/.test(message.text()) && sessions.has(url)
        && ['anonymous', 'logging-out'].includes(authPhase)) authConsole.push(url);
      else errors.push(`console: ${message.text()} ${url}`);
    });
  });
  context.on('response', response => {
    if (response.status() < 400) return;
    if (response.status() === 401 && response.request().method() === 'GET'
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) {
      authResponses.push(response.url());
    } else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  // Abort unexpected writes before they can change data, not just log them.
  await context.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() === 'GET') return route.continue();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen
      || url.origin !== base || url.search) {
      errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    pendingWrite.seen = true;
    return route.continue();
  });
  const assertClean = () => assert.deepEqual(errors, [], 'No unexpected HTTP/console/page errors or writes');
  const page = await context.newPage();
  async function uiWrite(method, pathname, action, status = 201) {
    assert.equal(pendingWrite, null);
    const expected = { key: `${method} ${pathname}`, seen: false };
    pendingWrite = expected;
    allowedWrites.push(expected.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi(method, pathname)), action()]);
      assert.equal(expected.seen, true, 'Allowed write must originate from the actual UI');
      const json = status === 204 ? null : await successfulJson(response, status);
      assert.equal(response.status(), status);
      assertClean();
      return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }

  await page.goto(`${base}/objekte`); // Avoid unrelated dashboard/T42 409.
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Mailweg 47'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await selectLabel(page, 'Verwaltungsart *', 'Mietobjekt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T47 synthetische Mietmahnung mit ausdruecklichen Quellrechten');
  const { json: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties',
    () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  assert.equal(new URL(page.url()).pathname, `/objekte/${property.id}`);
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  const { json: unit } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
  assert.equal(unit.property_id, property.id);
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitUrl = page.url();
  assert.ok(new URL(unitUrl).pathname.endsWith(`/${unit.id}`));

  await page.goto(`${base}/personen`);
  await button(page, '+ Neue Person').click();
  for (const [label, value] of [['Vorname', firstName], ['Nachname *', lastName],
    ['Strasse', 'Mieterweg 35'], ['PLZ', '12346'], ['Stadt', 'Mieterstadt'], ['E-Mail', 'tenant@example.invalid']]) await field(page, label).fill(value);
  const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
  assert.deepEqual([person.first_name, person.last_name, person.street, person.zip_code, person.city],
    [firstName, lastName, 'Mieterweg 35', '12346', 'Mieterstadt']);
  await searchList(page, 'Personen suchen');
  await page.getByRole('link', { name: personName, exact: true }).click();
  const personUrl = page.url();
  await page.goto(`${base}/kontaktdaten-rechte`);
  await field(page, 'Kontakt suchen').fill(firstName); // SearchChoice searches first name, not concatenated name.
  await selectId(page, 'Kontakt', person.id);
  await selectLabel(page, 'Begründung', 'Ersteinrichtung');
  const { json: domain } = await uiWrite('POST', '/api/person-data-domains',
    () => button(page, 'Datenbereich vorbereiten').click());
  assert.equal(domain.person_id, person.id);
  await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  await field(page, 'Bearbeiten').check();
  await field(page, 'Exportieren').check();
  await selectLabel(page, 'Begründung', 'Teamzugriff');
  const { json: contactGrant, payload: contactPayload } = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
    () => button(page, 'Teamfreigabe vorbereiten').click());
  assert.equal(contactGrant.user_id, principal.user_id);
  assert.equal(contactPayload.user_id, principal.user_id);
  assert.deepEqual([...contactGrant.capabilities].sort(), ['export', 'prepare', 'read']);
  assert.deepEqual(contactPayload.capabilities, ['export', 'prepare', 'read']);
  await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();
  await page.goto(personUrl);
  const { json: tenant } = await uiWrite('POST', '/api/tenant-profiles',
    () => button(page, 'Als Mieter kennzeichnen').click());
  assert.equal(tenant.person_id, person.id);
  await page.goto(unitUrl);
  await button(page, '+ Mietvertrag').click();
  await selectId(page, 'Mieter *', tenant.id);
  for (const [label, value] of [['Vertragsbeginn *', '2026-01-01'], ['Vertragsende', ''],
    ['Kaltmiete (EUR) *', '700.00'], ['NK-Vorauszahlung (EUR) *', '0.00']]) await field(page, label).fill(value);
  const { json: lease } = await uiWrite('POST', '/api/leases', () => button(page, 'Speichern').click());
  assert.equal(lease.unit_id, unit.id);
  assert.equal(lease.tenant_profile_id, tenant.id);
  assert.equal(lease.valid_from, '2026-01-01');
  assert.equal(lease.valid_to, null);
  equalDecimal(lease.base_rent, '700.00');
  equalDecimal(lease.advance_payment, '0.00');

  await page.goto(`${base}/berechtigungen`);
  await field(page.getByRole('group', { name: 'Objekt', exact: true }), 'Objekte durchsuchen').fill(propertyName);
  await selectLabel(page, 'Objekt', propertyName);
  await field(page, 'Bezeichnung').fill(scopeName);
  await selectLabel(page, 'Verwaltungsart', 'MIET');
  await field(page, 'Gültig ab').fill('2026-01-01');
  await field(page, 'Gültig bis (optional)').fill('');
  await field(page, 'Gesamtes Objekt').check();
  const { json: scope } = await uiWrite('POST', '/api/accounting-scopes',
    () => button(page, 'Bereich verbindlich anlegen').click());
  assert.equal(scope.property_id, property.id);
  assert.equal(scope.scope_type, 'MIET');
  assert.equal(scope.whole_property, true);
  assert.equal(scope.valid_from, '2026-01-01');
  assert.equal(scope.valid_to, null);
  await page.getByRole('link', { name: `${scopeName} – Teamrechte verwalten`, exact: true }).click();
  const grantForm = page.locator('form').filter({ has: page.getByRole('heading', { name: 'Teamfreigabe erteilen', exact: true }) });
  await field(page, 'Teammitglied suchen').fill('test@example.de');
  await selectId(page, 'Teammitglied', principal.user_id);
  for (const label of ['Lesen', 'Vorbereiten', 'Freigeben', 'Exportieren', 'Versenden']) await field(grantForm, label).check();
  for (const label of ['Bestätigen']) await field(grantForm, label).uncheck();
  await field(grantForm, 'Begründung').fill('T47 eigene MIET-Rechte zum Lesen, Vorbereiten, Ausstellen und PDF-Export und lokalen Testversand');
  const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/accounting-scopes/${scope.id}/grants`,
    () => button(grantForm, 'Freigabe erteilen').click());
  assert.equal(grant.user_id, principal.user_id);
  assert.equal(grantPayload.user_id, principal.user_id);
  assert.deepEqual([...grant.capabilities].sort(), ['approve', 'export', 'prepare', 'read', 'send']);
  assert.deepEqual([...grantPayload.capabilities].sort(), ['approve', 'export', 'prepare', 'read', 'send']);

  async function leaseSnapshot() {
    await page.goto(unitUrl);
    const [leases, history] = await freshReload(page, [response => isApi('GET', '/api/leases')(response)
      && new URL(response.url()).searchParams.get('unit_id') === unit.id,
    isApi('GET', `/api/leases/${lease.id}/rent-changes`)]);
    assert.equal(leases.filter(item => item.id === lease.id).length, 1);
    assertClean();
    return { leases, history };
  }
  const originalLease = await leaseSnapshot();
  await page.goto(`${base}/mahnungen`);
  const [settingsResponse] = await Promise.all([page.waitForResponse(isApi('GET', '/api/dunning/settings')),
    button(page, '+ Neue Mahnung').click()]);
  const settings = await successfulJson(settingsResponse);
  await selectLabel(page, 'Empfaengertyp *', 'Mieter');
  await selectId(page, 'Mieter *', tenant.id);
  await selectId(page, 'Mietvertrag *', lease.id);
  for (const [label, value] of [['Zeitraum von *', '2026-01-01'], ['Zeitraum bis *', '2026-01-31'],
    ['Stichtag', '2026-03-02'], ['Karenzzeit (Tage)', '0'], ['Mahngebuehr (Override)', '0']]) await field(page, label).fill(value);
  await field(page, 'Abrechnungskreis suchen').fill(scopeName);
  await selectId(page, 'Abrechnungskreis', scope.id);
  const basis = field(page, 'Basiszinssatz (%)');
  assert.equal(await basis.getAttribute('type'), 'text', 'Comma percent input is a real text field');
  if (settings.base_interest_rate == null) assert.equal(await basis.inputValue(), '', 'No configuration means blank, not implicit zero');
  else equalDecimal((await basis.inputValue()).replace(',', '.'), settings.base_interest_rate);
  for (const value of ['', 'keine-zahl']) {
    await basis.fill(value);
    const writes = actualWrites.length;
    const submit = button(page, 'Mahnung erstellen');
    if (await submit.isEnabled()) await submit.click();
    else assert.equal(await submit.isDisabled(), true);
    await page.waitForTimeout(300);
    assert.equal(actualWrites.length, writes, `Invalid basis ${JSON.stringify(value)} must not POST`);
    assert.equal(await basis.inputValue(), value);
    assertClean();
  }
  await basis.fill('2,50');
  const { json: notice, payload } = await uiWrite('POST', '/api/dunning/tenant',
    () => button(page, 'Mahnung erstellen').click());
  assert.equal(payload.tenant_profile_id, tenant.id);
  assert.equal(payload.lease_agreement_id, lease.id);
  assert.equal(payload.accounting_scope_id, scope.id);
  assert.equal(payload.period_start, '2026-01-01');
  assert.equal(payload.period_end, '2026-01-31');
  assert.equal(payload.reference_date, '2026-03-02');
  assert.equal(payload.grace_days, 0);
  equalDecimal(payload.override_fee, '0');
  equalDecimal(payload.base_interest_rate, '2.50');
  const expectedInterest = { base_interest_rate: '2.5', annual_rate: '7.5', reference_date: '2026-03-02',
    items: [{ due_date: '2026-01-01', days: 60, principal: '700', interest: '8.63',
      payment_type: 'KALTMIETE', period_year: 2026, period_month: 1 }] };
  function assertNotice(value, status) {
    assert.equal(value.id, notice.id);
    assert.equal(value.accounting_scope_id, scope.id);
    assert.equal(value.tenant_profile_id, tenant.id);
    assert.equal(value.status, status);
    equalDecimal(value.principal_amount, '700.00');
    equalDecimal(value.fee_amount, '0.00');
    equalDecimal(value.interest_amount, '8.63');
    equalDecimal(value.total_amount, '708.63');
    assert.deepEqual(interestSnapshot(value.interest_snapshot), expectedInterest);
  }
  assertNotice(notice, 'draft');
  await page.waitForURL(`${base}/mahnungen/${notice.id}`);
  const detailUrl = page.url();
  async function detailSnapshot(status) {
    await page.goto(detailUrl);
    const [stored] = await freshReload(page, [isApi('GET', `/api/dunning/${notice.id}`)]);
    assertNotice(stored, status);
    const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Verzugszinsen', exact: true }) });
    await row.waitFor();
    assert.match(await row.innerText(), /8[.,]63\s*EUR/);
    assertClean();
    return stored;
  }
  const draft = await detailSnapshot('draft');
  assert.deepEqual(draft, notice, 'Reload preserves complete draft DTO and frozen interest');
  let confirmedIssue = false;
  page.once('dialog', async dialog => {
    try {
      assert.equal(dialog.type(), 'confirm');
      assert.equal(dialog.message(), 'Mahnung ausgeben? Nach der Ausgabe ist die Mahnung unveraenderbar (GoBD). '
        + 'Der Entwurf wird endgültig als Mahnung gespeichert. Die Ausstellung weist keine Zustellung nach.');
      confirmedIssue = true;
      await dialog.accept();
    } catch (error) { errors.push(`Issue dialog: ${error.message}`); await dialog.dismiss(); }
  });
  const { json: issued } = await uiWrite('POST', `/api/dunning/${notice.id}/issue`,
    () => button(page, 'Mahnung ausgeben').click(), 200);
  assert.equal(confirmedIssue, true);
  assertNotice(issued, 'issued');
  assert.deepEqual(issued.interest_snapshot, draft.interest_snapshot, 'Issuance does not recalculate interest');
  assert.ok(issued.issued_at);
  const panel = page.getByRole('region', { name: 'E-Mail-Versand', exact: true });
  await panel.getByText('Noch keine Versandversuche.', { exact: true }).waitFor();
  await field(panel, 'E-Mail-Empfänger').selectOption(person.id);
  await panel.getByText('E-Mail: tenant@example.invalid', { exact: true }).waitFor();
  page.once('dialog', async dialog => {
    assert.equal(dialog.type(), 'confirm'); assert.ok(dialog.message().includes('tenant@example.invalid'));
    assert.ok(dialog.message().includes('kein Zustellnachweis')); await dialog.accept();
  });
  const { json: attempt, payload: sentPayload } = await uiWrite('POST', `/api/mail/dunning/${notice.id}`,
    () => button(panel, 'E-Mail-Versand bestätigen').click(), 200);
  assert.equal(sentPayload.person_id, person.id); assert.equal(sentPayload.request_id, attempt.id);
  assert.equal(attempt.status, 'accepted'); assert.equal(attempt.email, 'tenant@example.invalid');
  await panel.getByRole('cell', { name: 'SMTP angenommen (kein Zustellnachweis)', exact: true }).waitFor();
  assert.equal(smtpMessages.length, 1);
  assert.equal(smtpMessages[0].sender, '<verwaltung@example.invalid>');
  assert.deepEqual(smtpMessages[0].recipients, ['<tenant@example.invalid>']);
  const inspected = spawnSync('uv', ['run', 'python', '-c', `
import sys,json,base64
from email import policy
from email.parser import BytesParser
from io import BytesIO
import pdfplumber
message=BytesParser(policy=policy.default).parsebytes(sys.stdin.buffer.read())
attachments=list(message.iter_attachments())
assert len(attachments)==1 and attachments[0].get_content_type()=='application/pdf'
pdf=attachments[0].get_payload(decode=True)
with pdfplumber.open(BytesIO(pdf)) as document:
 text=' '.join(page.extract_text() or '' for page in document.pages)
print(json.dumps(dict(subject=str(message['Subject']),to=str(message['To']),message_id=str(message['Message-ID']),pdf=base64.b64encode(pdf).decode(),text=text)))
`], { cwd: path.resolve(__dirname, '../../backend'), input: smtpMessages[0].raw, encoding: 'utf8', timeout: 30000 });
  assert.ifError(inspected.error); assert.equal(inspected.status, 0, inspected.stderr);
  const mail = JSON.parse(inspected.stdout);
  assert.equal(mail.subject, 'Mahnung'); assert.equal(mail.to, 'tenant@example.invalid');
  assert.ok(mail.message_id.includes(attempt.id));
  for (const value of [firstName, lastName, '700,00', '8,63', '708,63']) assert.ok(mail.text.includes(value), `SMTP PDF missing ${value}: ${mail.text}`);
  await page.reload();
  await panel.getByRole('cell', { name: 'SMTP angenommen (kein Zustellnachweis)', exact: true }).waitFor();
  assert.equal(await panel.locator('tbody tr').count(), 1);
  const pdfPath = `/api/mail/dunning/${notice.id}/${attempt.id}/pdf`;
  const [popup, pdfResponse] = await Promise.all([page.waitForEvent('popup'),
    context.waitForEvent('response', { predicate: isApi('GET', pdfPath) }),
    panel.getByRole('link', { name: 'Versand-PDF öffnen', exact: true }).click()]);
  assert.equal(pdfResponse.status(), 200); assert.match(pdfResponse.headers()['content-type'], /^application\/pdf/);
  await popup.waitForLoadState('load');
  let bytes;
  try {
    bytes = await pdfResponse.body();
    if (!bytes.subarray(0, 5).equals(Buffer.from('%PDF-'))) throw new Error('Chrome returned PDF viewer HTML, not PDF resource bytes');
  } catch {
    const cdp = await context.newCDPSession(popup); let stream;
    try {
      const { frameTree } = await cdp.send('Page.getFrameTree');
      const { resource } = await cdp.send('Network.loadNetworkResource', { frameId: frameTree.frame.id, url: pdfResponse.url(), options: { disableCache: false, includeCredentials: true } });
      assert.equal(resource.success, true, JSON.stringify(resource)); assert.equal(resource.httpStatusCode, 200);
      stream = resource.stream; assert.ok(stream); const chunks = []; let length = 0;
      for (;;) {
        const part = await cdp.send('IO.read', { handle: stream, size: 65536 });
        const chunk = Buffer.from(part.data, part.base64Encoded ? 'base64' : 'utf8'); chunks.push(chunk); length += chunk.length;
        assert.ok(length < 25 * 1024 * 1024); if (part.eof) break;
      }
      bytes = Buffer.concat(chunks);
    } finally { try { if (stream) await cdp.send('IO.close', { handle: stream }); } finally { await cdp.detach(); } }
  }
  const attached = Buffer.from(mail.pdf, 'base64');
  const sha = value => require('node:crypto').createHash('sha256').update(value).digest('hex');
  assert.ok(bytes.equals(attached), `UI history opens exact SMTP attachment: ${JSON.stringify({actualLength: bytes.length, expectedLength: attached.length, actualPrefix: bytes.subarray(0, 40).toString('hex'), expectedPrefix: attached.subarray(0, 40).toString('hex'), actualHash: sha(bytes), expectedHash: sha(attached), url: pdfResponse.url()})}`);
  const text = spawnSync('pdftotext', ['-layout', '-', '-'], { input: bytes, encoding: 'utf8', timeout: 30000 });
  assert.ifError(text.error); assert.equal(text.status, 0, text.stderr); assert.ok(text.stdout.includes('708,63'));
  await popup.close(); await detailSnapshot('issued');
  assert.deepEqual(await leaseSnapshot(), originalLease, 'Mail leaves lease and rent history unchanged');
  await page.goto(detailUrl); await page.setViewportSize({ width: 390, height: 844 });
  await panel.getByRole('cell', { name: 'SMTP angenommen (kein Zustellnachweis)', exact: true }).waitFor();
  assert.equal(await panel.locator('tbody tr').count(), 1); assert.equal(smtpMessages.length, 1); assertClean();
  console.log('T47 UI PASS: UI-only scoped 700EUR dunning +8.63 interest=708.63; explicit send grant; selected synthetic contact; one real loopback SMTP message; exact MIME PDF and Message-ID; reload history; UI-opened retained PDF equals SMTP attachment and contains708.63; immutable notice/lease/history; phone viewport. SMTP acceptance is not delivery evidence.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  try { if (browser) await browser.close(); }
  finally { for (const socket of smtpSockets) socket.destroy(); await new Promise(resolve => smtpServer.close(resolve)); }
});
