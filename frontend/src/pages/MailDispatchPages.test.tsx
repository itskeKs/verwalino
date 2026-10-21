import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { DunningNoticeDetailPage } from "./DunningNoticeDetailPage";
import { SettlementDetailPage } from "./SettlementDetailPage";
import { MeetingDetailPage } from "./MeetingDetailPage";

vi.mock("../components/FinancialCoverageReview", () => ({ FinancialCoverageReview: () => null }));
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each([
  ["dunning", "issued", true], ["dunning", "draft", false], ["dunning", "cancelled", false],
  ["settlement", "finalized", true], ["settlement", "draft", false],
  ["invitation", "PLANNED", true], ["invitation", "HELD", true],
] as const)("integrates %s only in eligible document status %s", async (kind, status, visible) => {
  const fetch = vi.fn(async (url: string) => {
    if (url === `/api/mail/${kind}/doc1`) return json({ recipients: [], attempts: [] });
    if (url === "/api/dunning/doc1") return json({ id: "doc1", status, level: 1, included_items: [] });
    if (url.endsWith("/advance-proposal")) return new Response(JSON.stringify({ detail: "Kein Vorschlag für diese Test-Fixture" }), { status: 422 });
    if (url === "/api/settlements/doc1") return json({ id: "doc1", status, property_id: "property", run_type: "BK_MIETER", lines: [], extra_inputs: null });
    if (url === "/api/meetings/doc1") return json({ id: "doc1", status, meeting_date: "2026-11-15", meeting_type: "ORDENTLICH" });
    return json([]);
  });
  vi.stubGlobal("fetch", fetch);
  const path = kind === "dunning" ? "/mahnungen/:dunningId" : kind === "settlement" ? "/abrechnungen/:runId" : "/versammlungen/:meetingId";
  const entry = kind === "dunning" ? "/mahnungen/doc1" : kind === "settlement" ? "/abrechnungen/doc1" : "/versammlungen/doc1";
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u1", organization_id: "o1", role: "READ_ONLY" }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={[entry]}><Routes><Route path={path} element={kind === "dunning" ? <DunningNoticeDetailPage /> : kind === "settlement" ? <SettlementDetailPage /> : <MeetingDetailPage />} /></Routes></MemoryRouter>
  </AuthCtx.Provider>);
  await screen.findByRole("heading", { level: 1 });
  if (visible) {
    await screen.findByText("Noch keine Versandversuche.");
    expect(fetch).toHaveBeenCalledWith(`/api/mail/${kind}/doc1`, expect.objectContaining({ credentials: "include" }));
    expect(screen.queryByRole("button", { name: /Versand bestätigen/ })).toBeNull();
  } else {
    await waitFor(() => expect(screen.queryByRole("region", { name: "E-Mail-Versand" })).toBeNull());
    expect(fetch.mock.calls.some(([url]) => url.startsWith("/api/mail/"))).toBe(false);
  }
});
