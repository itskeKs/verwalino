import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScopePaymentReconciliation } from "./ScopePaymentReconciliation";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const candidate = { id: "payment", bank_label: "Mietkonto", value_date: "2025-06-01", direction: "IN", amount: "100.00", payment_type: "KALTMIETE", proof_status: "proven", blocker: null, source_digest: "a".repeat(64) };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

it("requires a review action, disables ambiguous rows and reuses the exact command after a failed response", async () => {
  const posts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_input: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      posts.push(options);
      return posts.length === 1 ? json({ detail: "Temporary failure" }, 503) : json({ items: [{}] }, 201);
    }
    return json({ items: [candidate, { ...candidate, id: "blocked", value_date: "2024-01-01", proof_status: "review_required", blocker: "binding_semantics_conflict" }], next_cursor: null });
  }));
  render(<ScopePaymentReconciliation scopeId="scope" />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Altzahlungen prüfen" }));
  const valid = await screen.findByRole("checkbox", { name: "Zahlung Mietkonto 2025-06-01 auswählen" });
  expect(screen.getByRole("checkbox", { name: "Zahlung Mietkonto 2024-01-01 auswählen" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Auswahl verbindlich zuordnen" })).toBeDisabled();
  fireEvent.click(valid);
  fireEvent.change(screen.getByLabelText("Begründung der Übernahme"), { target: { value: "Geprüftes Konto" } });
  fireEvent.click(screen.getByRole("button", { name: "Auswahl verbindlich zuordnen" }));
  await screen.findByRole("alert");
  expect(valid).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Auswahl verbindlich zuordnen" }));
  await screen.findByText("1 Zahlungen wurden zugeordnet. Die ursprünglichen Buchungen bleiben erhalten.");
  expect(posts).toHaveLength(2);
  expect(posts[0].headers).toEqual(posts[1].headers);
  expect(JSON.parse(String(posts[0].body))).toEqual({ reason: "Geprüftes Konto", items: [{ payment_id: "payment", source_digest: "a".repeat(64) }] });
  expect(posts[0].body).toBe(posts[1].body);
});

it("shows missing preview authority as unavailable and can recover without a false empty result", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ detail: "Accounting scope not accessible" }, 404))
    .mockResolvedValueOnce(json({ items: [], next_cursor: null })));
  render(<ScopePaymentReconciliation scopeId="scope" />);
  fireEvent.click(screen.getByRole("button", { name: "Altzahlungen prüfen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("nicht verfügbar");
  expect(screen.queryByText(/Keine weiteren Altzahlungen/)).toBeNull();
  expect(screen.getByRole("button", { name: "Auswahl verbindlich zuordnen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau neu laden" }));
  await screen.findByText("Keine weiteren Altzahlungen auf den zugeordneten Konten gefunden.");
});

it("drops selected evidence when a changed preview is explicitly reloaded", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ items: [candidate], next_cursor: null }))
    .mockResolvedValueOnce(json({ items: [{ ...candidate, source_digest: "b".repeat(64) }], next_cursor: null })));
  render(<ScopePaymentReconciliation scopeId="scope" />);
  fireEvent.click(screen.getByRole("button", { name: "Altzahlungen prüfen" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Vorschau neu laden" }));
  await waitFor(() => expect(screen.getByRole("checkbox")).not.toBeChecked());
  expect(screen.getByRole("button", { name: "Auswahl verbindlich zuordnen" })).toBeDisabled();
});
