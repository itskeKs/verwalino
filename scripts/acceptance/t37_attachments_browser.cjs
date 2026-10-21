#!/usr/bin/env node
/* T37: actual UI-only document upload/open/archive on a MIET unit.
 * Root starts scripts/dev-local.sh separately: UI 13000/API 18000, isolated
 * temporary document storage, exclusively the test DB at 127.0.0.1:55432.
 * This script starts no server, never seeds APIs/SQL, writes no fixture/output
 * files and performs no cleanup mutations. Synthetic UI records remain.
 * Run: node scripts/acceptance/t37_attachments_browser.cjs
 * Requires Chrome, Playwright, test@example.de/testpassword and backend uv
 * with pdfplumber. Only the exact UI-opened PDF URL is reread with the same
 * session, because CDP may expose Chrome viewer HTML instead of PDF bytes.
 * UI contract: region Dokumente; labels Dokumentdatei, Dokumenttitel,
 * Archivierungsgrund, Archivierte anzeigen; buttons Dokument hochladen,
 * Öffnen, Archivieren, Dokument archivieren, Abbrechen. No selector fallbacks.
 * Property is required; unit/person/lease are optional contextual parents.
 * No finance scope, lease, person or unrelated grants are fabricated here.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID, createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE
  || '/Users/keks/.npm/_npx/420ff84f11983ee5/node_modules/playwright');
const base = 'http://127.0.0.1:13000';
const prefix = `T37 ${Date.now().toString(36)} ${randomUUID().slice(0, 6)}`;
const propertyName = `${prefix} MIET`;
const unitName = `${prefix} Wohnung`;
const title = `${prefix} Vertragsoriginal`;
const filename = 't37-vertragsoriginal.pdf';
const reason = 'T37 synthetisches Vertragsoriginal revisionssicher archivieren';
const field = (scope, name) => scope.getByLabel(name, { exact: true });
const button = (scope, name) => scope.getByRole('button', { name, exact: true });
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const isApi = (method, pathname) => response => response.request().method() === method
  && new URL(response.url()).origin === base && new URL(response.url()).pathname === pathname;

// Fixed valid one-page PDF, including byte-accurate stream length and xref.
// Neither a PDF generator nor a workspace fixture is required.
function originalPdf() {
  const stream = 'BT /F1 18 Tf 50 750 Td (T37 Vertragsoriginal) Tj ET\n';
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream, 'ascii')} >>\nstream\n${stream}endstream`,
  ];
  let text = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(text, 'ascii'));
    text += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(text, 'ascii');
  text += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets.slice(1)) text += `${String(offset).padStart(10, '0')} 00000 n \n`;
  text += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(text, 'ascii');
}
const pdf = originalPdf();
const digest = sha256(pdf);
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
  assert.equal(response.status(), status,
    `${response.request().method()} ${response.url()}: ${response.status()} ${response.status() === status ? '' : await response.text()}`);
  return response.json();
}
// Request identity after main-frame commit, never a wall-clock cutoff or an
// old cached response. afterReload restores the explicit archived-list toggle.
async function freshReload(page, predicate, afterReload = async () => {}) {
  let committed = false;
  const requests = new Set();
  const navigation = frame => { if (frame === page.mainFrame()) committed = true; };
  const started = request => { if (committed) requests.add(request); };
  page.on('framenavigated', navigation);
  page.on('request', started);
  try {
    const pending = page.waitForResponse(response => requests.has(response.request()) && predicate(response));
    await page.reload();
    await afterReload();
    return await successfulJson(await pending);
  } finally {
    page.off('framenavigated', navigation);
    page.off('request', started);
  }
}
function assertMultipart(request, propertyId, unitId) {
  const contentType = request.headers()['content-type'] || '';
  const boundary = /boundary=(?:"([^"]+)"|([^;\s]+))/.exec(contentType);
  assert.match(contentType, /^multipart\/form-data;/i);
  assert.ok(boundary, 'Multipart boundary required');
  const body = request.postDataBuffer();
  assert.ok(body);
  const parts = body.toString('latin1').split(`--${boundary[1] || boundary[2]}`);
  const values = new Map();
  for (const part of parts.slice(1, -1)) {
    assert.ok(part.startsWith('\r\n') && part.endsWith('\r\n'));
    const split = part.indexOf('\r\n\r\n');
    assert.ok(split > 0);
    const headers = part.slice(2, split);
    const name = /\bname="([^"]+)"/.exec(headers);
    assert.ok(name);
    assert.equal(values.has(name[1]), false, `Duplicate multipart field ${name[1]}`);
    const value = Buffer.from(part.slice(split + 4, -2), 'latin1');
    values.set(name[1], value);
    if (name[1] === 'file') {
      assert.match(headers, /filename="t37-vertragsoriginal\.pdf"/);
      assert.match(headers, /Content-Type: application\/pdf/i);
      assert.deepEqual(value, pdf, 'UI sends the exact in-memory original');
    }
  }
  for (const key of values.keys()) assert.ok(['property_id', 'unit_id', 'person_id', 'lease_agreement_id', 'title', 'file'].includes(key), `Unexpected field ${key}`);
  assert.equal(values.get('property_id')?.toString(), propertyId);
  assert.equal(values.get('unit_id')?.toString(), unitId);
  assert.equal(values.get('title')?.toString(), title);
  assert.ok(values.has('file'));
  for (const optional of ['person_id', 'lease_agreement_id']) {
    assert.equal(values.get(optional)?.toString() || '', '', `${optional} must not be required or fabricated`);
  }
}
function assertDocument(value, propertyId, unitId, uploader) {
  assert.deepEqual(Object.keys(value).sort(), ['id', 'property_id', 'unit_id', 'lease_agreement_id', 'person_id',
    'title', 'original_filename', 'mime_type', 'content_byte_size', 'content_sha256', 'uploaded_by',
    'created_at', 'archived_at', 'archived_by', 'archive_reason'].sort(), 'Complete public DTO; no organization/storage URI');
  assert.match(value.id, /^[0-9a-f-]{36}$/i);
  assert.equal(value.property_id, propertyId);
  assert.equal(value.unit_id, unitId);
  assert.equal(value.lease_agreement_id, null);
  assert.equal(value.person_id, null);
  assert.equal(value.title, title);
  assert.equal(value.original_filename, filename);
  assert.equal(value.mime_type, 'application/pdf');
  assert.equal(value.content_byte_size, pdf.length);
  assert.equal(value.content_sha256, digest);
  assert.equal(value.uploaded_by, uploader);
  assert.ok(Number.isFinite(Date.parse(value.created_at)));
}
const originalMetadata = ({ archived_at, archived_by, archive_reason, ...original }) => original;
let browser;
(async () => {
  browser = await chromium.launch({ headless: true,
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const errors = [], actualWrites = [], allowedWrites = [], authResponses = [], authConsole = [];
  let authPhase = 'anonymous', pendingWrite = null;
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
      && sessions.has(response.url()) && ['anonymous', 'logging-out'].includes(authPhase)) authResponses.push(response.url());
    else errors.push(`HTTP ${response.status()}: ${response.request().method()} ${response.url()}`);
  });
  // Single-use permits: even duplicate requests from one click are blocked.
  // PATCH/DELETE/reparent/replace and all unrelated mutations have no permit.
  await context.route('**/api/**', async route => {
    const request = route.request();
    if (request.method() === 'GET') return route.continue();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    actualWrites.push(key);
    if (!pendingWrite || pendingWrite.key !== key || pendingWrite.seen || url.origin !== base || url.search) {
      errors.push(`Unexpected UI write: ${request.method()} ${request.url()}`);
      return route.abort('blockedbyclient');
    }
    pendingWrite.seen = true;
    return route.continue();
  });
  const assertClean = () => assert.deepEqual(errors, [], 'Strict HTTP/console/page/write guards');
  const page = await context.newPage();
  async function uiWrite(pathname, action, status = 201, multipart = false) {
    assert.equal(pendingWrite, null);
    const permit = { key: `POST ${pathname}`, seen: false };
    pendingWrite = permit;
    allowedWrites.push(permit.key);
    try {
      const [response] = await Promise.all([page.waitForResponse(isApi('POST', pathname)), action()]);
      assert.equal(permit.seen, true, 'Write must originate in actual UI');
      assert.equal(response.status(), status);
      const json = status === 204 ? null : await successfulJson(response, status);
      assertClean();
      return { json, request: response.request(), payload: multipart || status === 204 ? null : response.request().postDataJSON() };
    } finally { pendingWrite = null; }
  }
  await page.goto(`${base}/objekte`);
  await field(page, 'E-Mail').fill('test@example.de');
  await field(page, 'Passwort').fill('testpassword');
  const { json: principal } = await uiWrite('/api/auth/login', () => button(page, 'Anmelden').click(), 200);
  assert.equal(principal.role, 'OWNER_ADMIN');
  authPhase = 'authenticated';
  await page.getByRole('link', { name: 'Objekte', exact: true }).waitFor();
  await page.goto(`${base}/objekte`);
  await button(page, '+ Neues Objekt').click();
  for (const [label, value] of [['Name *', propertyName], ['Strasse', 'Synthetischer Dokumentenweg 37'],
    ['PLZ', '12345'], ['Stadt', 'Teststadt']]) await field(page, label).fill(value);
  await field(page, 'Verwaltungsart *').selectOption('MIET');
  await field(page, 'Datenbereich anlegen und mir Lesen, Bearbeiten, Freigeben und Exportieren erlauben').check();
  await field(page, 'Begründung der Freigabe').fill('T37 synthetische Dokumentenablage mit ausdruecklichen Quellrechten');
  const { json: property, payload: propertyPayload } = await uiWrite('/api/properties', () => button(page, 'Speichern').click());
  assert.equal(property.management_type, 'MIET');
  assert.equal(propertyPayload.data_audience.label, propertyName);
  assert.equal(propertyPayload.data_audience.grants.length, 1);
  assert.equal(propertyPayload.data_audience.grants[0].user_id, principal.user_id);
  assert.deepEqual([...propertyPayload.data_audience.grants[0].capabilities].sort(), ['approve', 'export', 'prepare', 'read']);
  await field(page, 'Objekte suchen').fill(prefix);
  await button(page.getByRole('search'), 'Suchen').click();
  await page.getByRole('link', { name: propertyName, exact: true }).click();
  assert.equal(new URL(page.url()).pathname, `/objekte/${property.id}`);
  const propertyUrl = page.url();
  await button(page, '+ Neue Einheit').click();
  await field(page, 'Nummer *').fill(unitName);
  await field(page, 'Flaeche (m2)').fill('70');
  const { json: unit } = await uiWrite('/api/units', () => button(page, 'Speichern').click());
  assert.equal(unit.property_id, property.id);
  await page.getByRole('link', { name: unitName, exact: true }).click();
  const unitUrl = page.url();
  assert.ok(new URL(unitUrl).pathname.endsWith(`/${unit.id}`));
  const panel = () => page.getByRole('region', { name: 'Dokumente', exact: true });
  const archiveDialog = () => page.getByRole('dialog', { name: 'Dokument archivieren', exact: true });
  const row = () => panel().getByRole('listitem').filter({ has: page.getByText(title, { exact: true }) });
  function listResponse(parent, archived = false) {
    return response => {
      if (!isApi('GET', '/api/attachments')(response)) return false;
      const query = new URL(response.url()).searchParams;
      return query.get('property_id') === property.id && query.get('unit_id') === (parent === 'unit' ? unit.id : null)
        && !query.get('person_id') && !query.get('lease_agreement_id')
        && (query.get('include_archived') === 'true') === archived && (!query.has('offset') || query.get('offset') === '0');
    };
  }
  await panel().waitFor();
  await field(panel(), 'Dokumentdatei').setInputFiles({ name: filename, mimeType: 'application/pdf', buffer: pdf });
  await field(panel(), 'Dokumenttitel').fill(title);
  const { json: uploaded, request: uploadRequest } = await uiWrite('/api/attachments',
    () => button(panel(), 'Dokument hochladen').click(), 201, true);
  assertMultipart(uploadRequest, property.id, unit.id);
  assertDocument(uploaded, property.id, unit.id, principal.user_id);
  assert.deepEqual([uploaded.archived_at, uploaded.archived_by, uploaded.archive_reason], [null, null, null]);
  await row().waitFor();
  assert.equal(await row().count(), 1);
  assert.ok((await row().innerText()).includes(filename), 'Visible list identifies original filename');
  async function snapshot(parent, expected, archived = false) {
    const list = await freshReload(page, listResponse(parent, archived), async () => {
      await panel().waitFor();
      if (archived) await field(panel(), 'Archivierte anzeigen').check();
    });
    assert.ok(Array.isArray(list.items));
    assert.equal(list.next_offset, null, 'Synthetic parent fits a single complete page');
    const matching = list.items.filter(item => item.id === uploaded.id);
    if (expected === null) {
      assert.deepEqual(matching, []);
      assert.equal(await row().count(), 0, 'Archived original hidden from default list');
    } else {
      assert.equal(matching.length, 1);
      assert.deepEqual(matching[0], expected, 'Fresh list preserves the complete public DTO');
      await row().waitFor();
      assert.equal(await row().count(), 1);
    }
    assertClean();
  }
  await snapshot('unit', uploaded);
  async function openOriginal() {
    const before = actualWrites.length;
    const pdfPath = `/api/attachments/${uploaded.id}/download`;
    const [tab, response] = await Promise.all([page.waitForEvent('popup'),
      context.waitForEvent('response', { predicate: response => isApi('GET', pdfPath)(response)
        && new URL(response.url()).search === '?inline=true' }), button(row(), 'Öffnen').click()]);
    assert.equal(response.status(), 200);
    assert.match(response.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
    assert.match(response.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
    await tab.waitForLoadState('load');
    assert.equal(tab.url(), `${base}${pdfPath}?inline=true`);
    assert.equal(tab.url(), response.url());
    assert.equal(await tab.evaluate(() => document.contentType), 'application/pdf');
    const viewerUrl = 'chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html';
    await eventually(async () => assert.ok(tab.frames().some(frame => frame.url() === viewerUrl), 'Real Chrome PDF viewer frame loaded'));
    await tab.frames().find(frame => frame.url() === viewerUrl).locator('pdf-viewer#viewer').waitFor({ state: 'attached' });
    const raw = await context.request.get(tab.url()); // Exact UI-opened URL, read-only, same session.
    assert.equal(raw.url(), tab.url());
    assert.equal(raw.status(), 200);
    assert.match(raw.headers()['content-type'] || '', /^application\/pdf(?:;|$)/i);
    assert.match(raw.headers()['content-disposition'] || '', /^inline(?:;|$)/i);
    const bytes = await raw.body();
    assert.equal(bytes.subarray(0, 5).toString(), '%PDF-');
    assert.ok(bytes.length > 0 && bytes.length < 25 * 1024 * 1024);
    assert.equal(sha256(bytes), digest);
    assert.deepEqual(bytes, pdf, 'Downloaded bytes are the uploaded original, including after archive');
    const parsed = spawnSync('uv', ['run', 'python', '-c',
      'import io,json,sys,pdfplumber\nwith pdfplumber.open(io.BytesIO(sys.stdin.buffer.read())) as pdf:\n print(json.dumps([page.extract_text() or "" for page in pdf.pages]))'],
    { cwd: path.resolve(__dirname, '../../backend'), input: bytes, encoding: 'utf8', timeout: 30000, maxBuffer: 8 * 1024 * 1024 });
    assert.ifError(parsed.error);
    assert.equal(parsed.status, 0, parsed.stderr);
    const pages = JSON.parse(parsed.stdout);
    assert.equal(pages.length, 1);
    assert.equal(pages[0].trim(), 'T37 Vertragsoriginal');
    await raw.dispose();
    await tab.close();
    assert.equal(actualWrites.length, before, 'Opening and raw PDF reread are read-only');
    assertClean();
    return bytes;
  }
  const beforeArchiveBytes = await openOriginal();
  await snapshot('unit', uploaded);
  // Property-context list must also include its linked unit document.
  await page.goto(propertyUrl);
  await snapshot('property', uploaded);
  await page.goto(unitUrl);
  await snapshot('unit', uploaded);
  const beforeCancel = actualWrites.length;
  await button(row(), 'Archivieren').click();
  await field(archiveDialog(), 'Archivierungsgrund').fill(reason);
  await button(archiveDialog(), 'Abbrechen').click();
  assert.equal(actualWrites.length, beforeCancel, 'Cancel sends no archive request');
  await snapshot('unit', uploaded);
  await button(row(), 'Archivieren').click();
  await field(archiveDialog(), 'Archivierungsgrund').fill('   ');
  const beforeBlank = actualWrites.length;
  await button(archiveDialog(), 'Dokument archivieren').click();
  assert.equal(actualWrites.length, beforeBlank, 'Whitespace-only archive reason never POSTs');
  assert.equal((await field(archiveDialog(), 'Archivierungsgrund').inputValue()).trim(), '');
  await field(archiveDialog(), 'Archivierungsgrund').fill(reason);
  const { json: archived, payload: archivePayload } = await uiWrite(`/api/attachments/${uploaded.id}/archive`,
    () => button(archiveDialog(), 'Dokument archivieren').click(), 200);
  assert.deepEqual(archivePayload, { reason });
  assertDocument(archived, property.id, unit.id, principal.user_id);
  assert.deepEqual(originalMetadata(archived), originalMetadata(uploaded), 'Archive never rewrites original metadata');
  assert.equal(archived.archived_by, principal.user_id);
  assert.ok(Number.isFinite(Date.parse(archived.archived_at)));
  assert.equal(archived.archive_reason, reason);
  await snapshot('unit', null);
  await snapshot('unit', archived, true);
  assert.deepEqual(await openOriginal(), beforeArchiveBytes);
  await snapshot('unit', archived, true);
  await page.goto(propertyUrl);
  await snapshot('property', null);
  await snapshot('property', archived, true);
  assert.deepEqual(actualWrites, ['POST /api/auth/login', 'POST /api/properties', 'POST /api/units',
    'POST /api/attachments', `POST /api/attachments/${uploaded.id}/archive`], 'Exactly one upload/archive; no financial, replacement or reparenting mutations');
  assert.deepEqual(actualWrites, allowedWrites);
  authPhase = 'logging-out';
  await uiWrite('/api/auth/logout', () => button(page, 'Abmelden').click(), 204);
  await button(page, 'Anmelden').waitFor();
  await context.close();
  assert.deepEqual([...authResponses].sort(), [...authConsole].sort(), 'Only exact local anonymous/logout session 401s, paired 1:1');
  assert.deepEqual(actualWrites, allowedWrites);
  assertClean();
  console.log('PASS T37: UI-only MIET/unit setup and grants; one exact PDF upload; fresh complete DTOs; Chrome original before/after archive; cancel/blank guards; property/unit lists; no unexpected mutations.', propertyName);
})().catch(error => { console.error(error); process.exitCode = 1; })
  .finally(async () => { if (browser) await browser.close(); });
