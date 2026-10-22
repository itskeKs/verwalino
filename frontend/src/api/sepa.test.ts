import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { getSepaAccount, saveSepaCreditor, createSepaMandate, updateSepaMandate, revokeSepaMandate, previewSepa, downloadSepaXml, type SepaExportRequest } from "./sepa";

const request: SepaExportRequest = { bank_account_id: "bank/1", accounting_scope_id: null, year: 2026, month: 10, collection_date: "2026-10-20", message_id: "MONTH-2026-10", pre_notification_on: "2026-10-06", notice_period_days: 14, shorter_period_agreed: false, pre_notification_confirmed: true, bank_profile_confirmed: true };
afterEach(() => vi.unstubAllGlobals());
it("encodes account IDs and loads the complete saved account", async () => {
  const value = { bank: { id: "bank/1" }, creditor: null, tenants: [], mandates: [] };
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(value)));
  vi.stubGlobal("fetch", fetch);
  expect(await getSepaAccount("bank/1")).toEqual(value);
  expect(fetch).toHaveBeenCalledWith("/api/payments/sepa/accounts/bank%2F1", expect.objectContaining({ credentials: "include" }));
});
it("posts only explicit creditor and signed mandate configuration", async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response("{}")); vi.stubGlobal("fetch", fetch);
  const creditor = { creditor_id: "DE98ZZZ09999999999", name: "Creditor", country: "DE", town: "Berlin", bank_authorized: true as const };
  await saveSepaCreditor("bank/1", creditor);
  const mandate = { tenant_profile_id: "tenant", reference: "SIGNED-1", signed_on: "2026-01-01", sequence_type: "RCUR" as const, last_used_on: "2026-09-20", debtor_country: "DE", confirmed_iban: "DE89370400440532013000", original_reference: "External archive 42", mandate_confirmed: true as const };
  await createSepaMandate("bank/1", mandate);
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/payments/sepa/accounts/bank%2F1/creditor", expect.objectContaining({ method: "PUT", body: JSON.stringify(creditor) }));
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/payments/sepa/accounts/bank%2F1/mandates", expect.objectContaining({ method: "POST", body: JSON.stringify(mandate) }));
});
it("updates only sequence history and revokes without inventing an execution", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response("{}" )).mockResolvedValueOnce(new Response(null, { status: 204 })); vi.stubGlobal("fetch", fetch);
  await updateSepaMandate("m/1", { sequence_type: "RCUR", last_used_on: "2026-09-20" });
  await revokeSepaMandate("m/1");
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/payments/sepa/mandates/m%2F1", expect.objectContaining({ method: "PUT", body: JSON.stringify({ sequence_type: "RCUR", last_used_on: "2026-09-20" }) }));
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/payments/sepa/mandates/m%2F1/revoke", expect.objectContaining({ method: "POST" }));
});
it("preserves decimal preview strings and downloads exact XML with expected digest", async () => {
  const preview = { rows: [], total: "12345678901234567890.91", source_digest: "digest" };
  const xml = "<?xml version=\"1.0\"?><Document>CORE</Document>";
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(preview))).mockResolvedValueOnce(new Response(xml)); vi.stubGlobal("fetch", fetch);
  expect(await previewSepa(request)).toEqual(preview);
  expect(await (await downloadSepaXml({ ...request, expected_source_digest: "digest" })).text()).toBe(xml);
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/payments/sepa/preview", expect.objectContaining({ method: "POST", body: JSON.stringify(request) }));
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/payments/sepa.xml", expect.objectContaining({ method: "POST", credentials: "include", body: JSON.stringify({ ...request, expected_source_digest: "digest" }) }));
});
it.each([403, 409, 422, 500])("propagates %s without a fake file", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Vollständige Mandate erforderlich" }), { status })));
  await expect(downloadSepaXml({ ...request, expected_source_digest: "digest" })).rejects.toEqual(new ApiError(status, "Vollständige Mandate erforderlich"));
});
