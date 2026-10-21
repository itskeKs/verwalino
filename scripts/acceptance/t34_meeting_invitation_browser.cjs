#!/usr/bin/env node
/* T34: real UI preparation and read-only invitation PDF export.
 * Root starts scripts/dev-local.sh separately (UI 13000/API 18000), after
 * backend/frontend checks. This script starts no server and accesses no DB.
 * Run: node scripts/acceptance/t34_meeting_invitation_browser.cjs
 * Prerequisites: test@example.de/testpassword, Chrome, Playwright, backend uv
 * environment with pdfplumber, regular property/contact source-rights forms.
 * New contract: MeetingDetail button "Einladung als PDF" opens a new Chrome
 * tab at GET /api/meetings/{id}/invitation.pdf (200, application/pdf, inline).
 * All writes use actual UI forms, with an exact, one-request-at-a-time allowlist.
 * No API/SQL seeding, mocks, issue/send/storage actions, output files or cleanup
 * mutations. Synthetic records remain. Only the exact UI-opened PDF URL is
 * reread using the same session to obtain bytes hidden by Chrome's PDF viewer.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T34 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} WEG`;
const location = `${prefix} Gemeinschaftssaal`;
const topTitles = [`${prefix} Begruessung`, `${prefix} Instandhaltung`];
const invitationText = `${prefix} Einladung zur ordentlichen Eigentuemerversammlung.`;
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const normalized = text => text.trim().replace(/\s+/g, ' ');
function calendarDate(date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')].join('-');
}
// Calendar days from the real clock, not a fixed 2026 fixture or mocked clock.
const today = new Date();
const future = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 20, 12);
const meetingDate = calendarDate(future);
const ownershipStart = `${today.getFullYear()}-01-01`;
let browser;

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
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).pathname === pathname;
async function successfulJson(response, status = 200) {
  if (response.status() !== status) {
    assert.fail(`${response.request().method()} ${response.url()}: ${response.status()} ${await response.text()}`);
  }
  return response.json();
}
async function selectLabel(page, label, optionLabel) {
  const select = page.getByRole('combobox', { name: label, exact: true });
  await select.getByRole('option', { name: optionLabel, exact: true }).waitFor({ state: 'attached' });
  await select.selectOption({ label: optionLabel });
}
async function selectObservedId(page, label, id) {
  const select = field(page, label);
  await eventually(async () => assert.ok((await select.getByRole('option').evaluateAll(
    options => options.map(option => option.value))).includes(id), `${label}: missing observed ID ${id}`));
  await select.selectOption(id);
}
async function searchList(page, label) {
  await field(page, label).fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
}
// Ignore responses belonging to the old document. All snapshot requests must
// start after a fresh main-frame commit, not a previous React reload's tail.
async function freshReload(page, predicates) {
  let committed = false;
  const freshRequests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) freshRequests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    const responses = predicates.map(predicate => page.waitForResponse(response =>
      freshRequests.has(response.request()) && predicate(response)));
    const result = await Promise.all([...responses, page.reload()]);
    return await Promise.all(result.slice(0, predicates.length).map(response => successfulJson(response)));
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
  }
}
function canonicalDecimal(value) {
  assert.match(String(value), /^\d+(?:\.\d+)?$/);
  return String(value).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
}
function sortedRecords(records) {
  return records.map(record => ({ ...record, mea_share: record.mea_share == null ? null : canonicalDecimal(record.mea_share) }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

(async () => {
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
  context.on('request', request => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') || request.method() === 'GET') return;
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen
      || url.origin !== base || url.search) errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
    else pendingWrite.seen = true;
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
      assert.equal(expected.seen, true, 'The allowed write must be observed from the UI');
      const json = status === 204 ? null : await successfulJson(response, status);
      assert.equal(response.status(), status);
      assertClean();
      return { json, payload: response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }

  // Preserve /objekte as login destination; do not visit the unrelated dashboard.
  await page.goto(`${base}/objekte`);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await uiWrite('POST', '/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN'); // regular setup role, never a source-access bypass
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  await field(page, 'Name *').fill(propertyName);
  await selectLabel(page, 'Verwaltungsart *', 'WEG');
  await field(page, 'Strasse').fill('Synthetischer Versammlungsweg 34');
  await field(page, 'PLZ').fill('12345');
  await field(page, 'Stadt').fill('Teststadt');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T34 synthetische Einladung mit ausdruecklichen Quelldatenrechten');
  const { json: property, payload: propertyPayload } = await uiWrite('POST', '/api/properties',
    () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'WEG');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await searchList(page, 'Objekte suchen');
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  const propertyUrl = page.url();
  assert.equal(new URL(propertyUrl).pathname, `/objekte/${property.id}`);
  await field(page, 'Nenner *').fill('2');
  await uiWrite('POST', `/api/properties/${property.id}/mea-scale/confirm`,
    () => button(page, 'MEA-Skala bestätigen').click(), 200);
  await field(page, 'Nenner *').waitFor({ state: 'hidden' });

  const units = [];
  for (let i = 1; i <= 2; i++) {
    if (i > 1) await page.goto(propertyUrl);
    await button(page, '+ Neue Einheit').click();
    const unitName = `${prefix} Wohnung ${i}`;
    await field(page, 'Nummer *').fill(unitName);
    await field(page, 'Flaeche (m2)').fill('70');
    await field(page, 'MEA (Miteigentumsanteil)').fill('1');
    const { json: unit } = await uiWrite('POST', '/api/units', () => button(page, 'Speichern').click());
    assert.equal(unit.property_id, property.id);
    assert.equal(canonicalDecimal(unit.mea), '1');
    await page.getByRole('link', { name: unitName, exact: true }).click();
    const url = page.url();
    assert.ok(new URL(url).pathname.endsWith(`/${unit.id}`));
    units.push({ ...unit, url });
  }

  const owners = [];
  for (const [index, first, last, street, zip, city] of [
    [0, 'Ada', 'Linden', 'Lindenweg 11', '12341', 'Lindenstadt'],
    [1, 'Berta', 'Weber', 'Weberweg 22', '12342', 'Weberstadt'],
  ]) {
    const firstName = `${prefix} ${first}`;
    const name = `${firstName} ${last}`;
    await page.goto(`${base}/personen`);
    await button(page, '+ Neue Person').click();
    for (const [label, value] of [['Vorname', firstName], ['Nachname *', last],
      ['Strasse', street], ['PLZ', zip], ['Stadt', city]]) await field(page, label).fill(value);
    const { json: person } = await uiWrite('POST', '/api/persons', () => button(page, 'Speichern').click());
    assert.equal(person.first_name, firstName);
    assert.equal(person.last_name, last);
    assert.deepEqual([person.street, person.zip_code, person.city], [street, zip, city]);
    await searchList(page, 'Personen suchen');
    await page.getByRole('link', { name, exact: true }).click();
    const personUrl = page.url();

    // Explicit current-contact source grants, not administrative fallback or
    // an invented financial accounting scope. Grant before profile creation.
    await page.goto(`${base}/kontaktdaten-rechte`);
    await field(page, 'Kontakt suchen').fill(firstName);
    await selectObservedId(page, 'Kontakt', person.id);
    await selectLabel(page, 'Begründung', 'Ersteinrichtung');
    const { json: domain } = await uiWrite('POST', '/api/person-data-domains',
      () => button(page, 'Datenbereich vorbereiten').click());
    assert.equal(domain.person_id, person.id);
    await page.getByRole('link', { name: `${domain.label} – Teamfreigaben vorbereiten`, exact: true }).click();
    await field(page, 'Teammitglied suchen').fill('test@example.de');
    await selectObservedId(page, 'Teammitglied', principal.user_id);
    await field(page, 'Bearbeiten').check();
    await field(page, 'Exportieren').check();
    await selectLabel(page, 'Begründung', 'Teamzugriff');
    const { json: grant, payload: grantPayload } = await uiWrite('POST', `/api/person-data-domains/${domain.id}/grants`,
      () => button(page, 'Teamfreigabe vorbereiten').click());
    assert.equal(grant.user_id, principal.user_id);
    assert.equal(grantPayload.user_id, principal.user_id);
    assert.deepEqual([...grant.capabilities].sort(), ['export', 'prepare', 'read']);
    assert.deepEqual(grantPayload.capabilities, ['export', 'prepare', 'read']);
    await page.getByRole('cell', { name: 'Vorbereitet ohne Ablauf', exact: true }).waitFor();

    await page.goto(personUrl);
    const { json: owner } = await uiWrite('POST', '/api/owner-profiles',
      () => button(page, 'Als Eigentuemer kennzeichnen').click());
    assert.equal(owner.person_id, person.id);
    await page.getByText('Eigentuemer', { exact: true }).waitFor();
    await page.goto(units[index].url);
    await button(page, '+ Eigentum').click();
    await selectLabel(page, 'Eigentuemer *', name);
    await field(page, 'Gueltig ab *').fill(ownershipStart);
    await field(page, 'Gueltig bis').fill('');
    await field(page, 'MEA-Anteil').fill('1');
    const { json: ownership } = await uiWrite('POST', '/api/ownership', () => button(page, 'Speichern').click());
    assert.equal(ownership.unit_id, units[index].id);
    assert.equal(ownership.owner_profile_id, owner.id);
    assert.equal(ownership.valid_from, ownershipStart);
    assert.equal(ownership.valid_to, null);
    assert.equal(canonicalDecimal(ownership.mea_share), '1');
    await page.getByRole('cell', { name, exact: true }).waitFor();
    owners.push({ ...owner, name, street, zip, city, ownership });
  }

  await page.goto(`${base}/versammlungen`);
  await button(page, '+ Neue Versammlung').click();
  await selectLabel(page, 'WEG-Objekt *', propertyName);
  await field(page, 'Datum *').fill(meetingDate);
  await selectLabel(page, 'Typ *', 'Ordentlich');
  await field(page, 'Ort').fill(location);
  await field(page, 'Einladungstext').fill(invitationText);
  const { json: meeting } = await uiWrite('POST', '/api/meetings', () => button(page, 'Versammlung anlegen').click());
  assert.equal(meeting.property_id, property.id);
  assert.equal(meeting.meeting_date, meetingDate);
  assert.equal(meeting.location, location);
  assert.equal(meeting.status, 'PLANNED');
  assert.equal(meeting.invitation_text, invitationText);
  await page.waitForURL(`${base}/versammlungen/${meeting.id}`);
  const meetingUrl = page.url();
  const agenda = [];
  for (let index = 0; index < topTitles.length; index++) {
    await button(page, '+ TOP hinzufuegen').click();
    await field(page, 'Titel *').fill(topTitles[index]);
    await field(page, 'Beschreibung').fill(`${prefix} Beratung ${index + 1}`);
    const { json: item } = await uiWrite('POST', `/api/meetings/${meeting.id}/agenda`,
      () => button(page, 'TOP anlegen').click());
    assert.equal(item.meeting_id, meeting.id);
    assert.equal(item.item_number, index + 1);
    assert.equal(item.title, topTitles[index]);
    agenda.push(item);
    await field(page, 'Titel *').waitFor({ state: 'hidden' });
    await page.getByText(`TOP ${index + 1}:`, { exact: true }).waitFor();
    await eventually(async () => assert.ok(normalized(await page.locator('body').innerText()).includes(topTitles[index])));
  }

  async function snapshots() {
    const ownership = [];
    for (let index = 0; index < units.length; index++) {
      const unit = units[index];
      await page.goto(unit.url);
      const [records] = await freshReload(page, [response => isApi('GET', '/api/ownership')(response)
        && new URL(response.url()).searchParams.get('unit_id') === unit.id]);
      assert.deepEqual(sortedRecords(records), sortedRecords([owners[index].ownership]));
      await page.getByRole('cell', { name: owners[index].name, exact: true }).waitFor();
      ownership.push(sortedRecords(records));
    }
    await page.goto(meetingUrl);
    const [storedMeeting, storedAgenda, resolutions] = await freshReload(page, [
      isApi('GET', `/api/meetings/${meeting.id}`),
      isApi('GET', `/api/meetings/${meeting.id}/agenda`),
      isApi('GET', `/api/meetings/${meeting.id}/resolutions`),
    ]);
    assert.deepEqual(storedMeeting, meeting);
    assert.deepEqual(storedAgenda, agenda, 'Exactly two TOPs in their original order');
    assert.deepEqual(resolutions, [], 'Invitation creates no resolution or register effect');
    await page.getByRole('cell', { name: meetingDate, exact: true }).waitFor();
    await page.getByRole('cell', { name: location, exact: true }).waitFor();
    for (let index = 0; index < 2; index++) await page.getByText(`TOP ${index + 1}:`, { exact: true }).waitFor();
    assertClean();
    return { meeting: storedMeeting, agenda: storedAgenda, ownership, resolutions };
  }
  const before = await snapshots();
  const writesBeforePdf = actualWrites.length;
  const invitationPath = `/api/meetings/${meeting.id}/invitation.pdf`;
  const [pdfPage, pdfResponse] = await Promise.all([
    page.waitForEvent('popup'),
    context.waitForEvent('response', { predicate: isApi('GET', invitationPath) }),
    button(page, 'Einladung als PDF').click(),
  ]);
  assert.equal(pdfResponse.status(), 200);
  assert.match(pdfResponse.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
  assert.match(pdfResponse.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
  await pdfPage.waitForLoadState('load');
  assert.equal(pdfPage.url(), pdfResponse.url(), 'Actual tab shows the exact UI-opened PDF response URL');
  assert.equal(new URL(pdfPage.url()).origin, base);
  assert.equal(new URL(pdfPage.url()).pathname, invitationPath);
  assert.equal(new URL(pdfPage.url()).search, '');
  assert.equal(await pdfPage.evaluate(() => document.contentType), 'application/pdf');
  const viewerUrl = 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html';
  await eventually(async () => assert.ok(pdfPage.frames().some(frame => frame.url() === viewerUrl),
    'Chrome PDF viewer frame must load'));
  const viewerFrame = pdfPage.frames().find(frame => frame.url() === viewerUrl);
  await viewerFrame.locator('pdf-viewer#viewer').waitFor({ state: 'attached' });
  // CDP may return the viewer HTML. No refetch of other URLs or business APIs.
  const rawPdf = await context.request.get(pdfPage.url());
  assert.equal(rawPdf.url(), pdfPage.url());
  assert.equal(rawPdf.status(), 200);
  assert.match(rawPdf.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
  assert.match(rawPdf.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
  const bytes = await rawPdf.body();
  assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
  assert.ok(bytes.length < 25 * 1024 * 1024, 'PDF must be smaller than 25 MiB');
  // Pure in-memory PDF parsing only: no DB, application imports or file output.
  const parsed = spawnSync('uv', ['run', 'python', '-c',
    'import io,json,sys,pdfplumber\nwith pdfplumber.open(io.BytesIO(sys.stdin.buffer.read())) as pdf:\n print(json.dumps([page.extract_text() or "" for page in pdf.pages]))'],
  { cwd: path.resolve(__dirname, '../../backend'), input: bytes, encoding: 'utf8', timeout: 30000,
    maxBuffer: 8 * 1024 * 1024 });
  assert.ifError(parsed.error);
  assert.equal(parsed.status, 0, parsed.stderr);
  const pages = JSON.parse(parsed.stdout).map(normalized);
  assert.equal(pages.length, 2, 'Exactly two pages: one personalized invitation per owner');
  const germanDate = meetingDate.split('-').reverse().join('.');
  const seenOwners = new Set();
  for (const text of pages) {
    const recipients = owners.filter(owner => text.includes(owner.name));
    assert.equal(recipients.length, 1, 'Each page names exactly one of the two different recipients');
    const owner = recipients[0];
    assert.ok(!seenOwners.has(owner.id), 'Do not duplicate the same recipient on both pages');
    seenOwners.add(owner.id);
    for (const value of [owner.street, owner.zip, owner.city, propertyName, location, ...topTitles]) {
      assert.ok(text.includes(value), `Invitation page missing ${value}`);
    }
    const other = owners.find(candidate => candidate.id !== owner.id);
    assert.ok(!text.includes(other.street), 'Other owner postal address must not leak onto recipient page');
    assert.ok(text.includes(meetingDate) || text.includes(germanDate), 'Invitation contains meeting date');
    assert.match(text, /TOP\s*1\b/i);
    assert.match(text, /TOP\s*2\b/i);
    assert.ok(text.indexOf(topTitles[0]) < text.indexOf(topTitles[1]), 'TOP 1 precedes TOP 2');
    assert.match(text, /(?:21\s*(?:Tage|Tagen|Tages)|drei\s+Wochen)/i, 'Three-week invitation deadline is stated');
    assert.match(text, /Warnung|Hinweis|Frist/i, 'Short-notice warning is visible');
    assert.match(text, /unterschritt|weniger|zu kurz|nicht eingehalten|unter\s+21|20\s*Tage/i,
      'Meeting in 20 days warns that the 21-day period is not met');
  }
  assert.equal(seenOwners.size, 2);
  assert.equal(calendarDate(new Date()), calendarDate(today), 'Rerun if actual today changed during acceptance');
  await pdfPage.close();
  const after = await snapshots();
  assert.deepEqual(after, before, 'PDF GET leaves meeting, ordered agenda and ownership records unchanged');
  assert.equal(actualWrites.length, writesBeforePdf, 'PDF export and persisted-state observations are read-only');
  assert.deepEqual(actualWrites, allowedWrites, 'Exact UI-write allowlist; no issue/send/storage or other mutations');
  authPhase = 'logging-out';
  await uiWrite('POST', '/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await button(page, 'Anmelden').waitFor();
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(),
    'Only exact anonymous/logout session-401 responses and console errors are permitted, paired 1:1');
  assert.deepEqual(actualWrites, allowedWrites);
  assertClean();
  console.log('PASS T34: real UI setup, explicit source grants, new Chrome invitation PDF tab, two personalized pages, short-notice warning and unchanged business data.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
