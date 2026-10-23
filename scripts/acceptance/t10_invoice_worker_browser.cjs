#!/usr/bin/env node
/* T10: upload a synthetic invoice through the UI and await the real local worker.
 * Start scripts/dev-local.sh first. No API writes outside login/upload, fixtures,
 * extraction overrides, cost adoption, production access or persisted reports.
 */
'use strict';
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const filename = `T10-synthetic-${randomUUID()}.pdf`;

function syntheticInvoice() {
  const lines = ['Stadtwerke Test GmbH - SYNTHETIC INVOICE',
    'Rechnungsdatum: 01.06.2025', 'Gesamtbetrag: 119,00 EUR'];
  const stream = `BT /F1 16 Tf 60 750 Td 40 TL\n${lines.map((line, i) =>
    `${i ? 'T* ' : ''}(${line}) Tj`).join('\n')}\nET\n`;
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`];
  let pdf = '%PDF-1.4\n';
  const offsets = [];
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('');
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf);
}

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.HV_CHROMIUM_EXECUTABLE
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
    const context = await browser.newContext({ serviceWorkers: 'block' });
    const failures = [];
    const allowedOrigins = new Set([base, 'http://127.0.0.1:18000']);
    await context.route('**/*', route => {
      const request = route.request();
      const url = new URL(request.url());
      if (!allowedOrigins.has(url.origin)) {
        failures.push(`Unexpected external request: ${url.origin}`);
        return route.abort();
      }
      if (!['GET', 'HEAD'].includes(request.method()) &&
          !(request.method() === 'POST' && ['/api/auth/login', '/api/invoice-documents/upload'].includes(url.pathname))) {
        failures.push(`Unexpected write: ${request.method()} ${url.pathname}`);
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => failures.push(error.message));
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.pathname.startsWith('/api/') && response.status() >= 400 &&
          !(url.pathname === '/api/auth/session' && response.status() === 401)) {
        failures.push(`HTTP ${response.status()}: ${url.pathname}`);
      }
    });
    await page.goto(`${base}/belege`);
    await page.getByLabel('E-Mail', { exact: true }).fill('test@example.de');
    await page.getByLabel('Passwort', { exact: true }).fill('testpassword');
    const login = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login');
    await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
    assert.equal((await login).status(), 200);
    await page.getByRole('heading', { name: 'Belege', exact: true }).waitFor();
    const buffer = process.env.HV_T10_INVOICE_PDF ? readFileSync(process.env.HV_T10_INVOICE_PDF) : syntheticInvoice();
    await page.getByLabel('Belegdatei', { exact: true }).setInputFiles({ name: filename, mimeType: 'application/pdf', buffer });
    const upload = page.waitForResponse(response => response.request().method() === 'POST' &&
      new URL(response.url()).pathname === '/api/invoice-documents/upload');
    await page.getByRole('button', { name: 'Hochladen', exact: true }).click();
    const response = await upload;
    assert.equal(response.status(), 201, await response.text());
    const original = await response.json();
    assert.equal(original.processing_status, 'queued');
    assert.equal(original.extraction_payload?.gross_amount ?? null, null);
    assert.equal(original.extraction_payload?.invoice_date ?? null, null);
    assert.equal(original.cost_entry_id, null);
    const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: filename, exact: true }) });
    const completed = page.waitForResponse(response => new URL(response.url()).pathname === `/api/invoice-documents/${original.id}` &&
      response.request().method() === 'GET' && response.status() === 200 &&
      response.json().then(document => document.processing_status === 'succeeded'), { timeout: 150000 });
    await row.getByRole('link', { name: 'Details', exact: true }).click();
    const processed = await (await completed).json();
    assert.equal(processed.extraction_payload.gross_amount, '119.00');
    assert.equal(processed.extraction_payload.invoice_date, '2025-06-01');
    assert.equal(processed.cost_entry_id, null);
    assert.equal(processed.released_at, null);
    assert.equal(processed.content_sha256, original.content_sha256);
    await page.getByRole('status').filter({ hasText: 'Felder vorbereitet' }).waitFor();
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Bruttobetrag', exact: true }) })
      .getByRole('cell', { name: /^119\.00(?:\s*\(\d+%\))?$/ }).waitFor();
    await page.getByRole('row').filter({ has: page.getByRole('cell', { name: 'Rechnungsdatum', exact: true }) })
      .getByRole('cell', { name: /^2025-06-01(?:\s*\(\d+%\))?$/ }).waitFor();
    assert.deepEqual(failures, []);
    console.log(`T10 UI PASS: ${filename}; queued -> succeeded; 119.00 EUR; 2025-06-01; original unchanged; no cost/release`);
  } finally {
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
