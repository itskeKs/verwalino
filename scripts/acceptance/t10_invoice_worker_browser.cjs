#!/usr/bin/env node
/* T10: upload a synthetic structured invoice through the UI and await the real local worker.
 * A structured invoice (XRechnung) is read by the deterministic parser: no provider, key or model is involved.
 * Start scripts/dev-local.sh first. No API writes outside login/upload, fixtures,
 * extraction overrides, cost adoption, production access or persisted reports.
 */
'use strict';
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.HV_PLAYWRIGHT_PACKAGE || 'playwright');
const base = 'http://127.0.0.1:13000';
const filename = `T10-synthetic-${randomUUID()}.xml`;

function syntheticInvoice() {
  return Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">
  <cbc:ID>T10-SYNTHETIC</cbc:ID>
  <cbc:IssueDate>2025-06-01</cbc:IssueDate>
  <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>Stadtwerke Test GmbH - SYNTHETIC INVOICE</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
  <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">19.00</cbc:TaxAmount>
    <cac:TaxSubtotal><cac:TaxCategory><cbc:Percent>19.0</cbc:Percent></cac:TaxCategory></cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxInclusiveAmount currencyID="EUR">119.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">119.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
</Invoice>`);
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
    await page.getByLabel('Belegdatei', { exact: true }).setInputFiles({ name: filename, mimeType: 'application/xml', buffer: syntheticInvoice() });
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
