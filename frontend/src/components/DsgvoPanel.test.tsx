import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DsgvoPanel } from "./DsgvoPanel";
import * as endpoints from "../api/endpoints";
import type { DsgvoStatus } from "../api/types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("removes a previous export when renewed access is denied", async () => {
  const status: DsgvoStatus = { person_id: "one", applied: "NONE", already_anonymized: false,
    anonymized_at: null, processing_restricted: false, deletion_requested_at: null,
    has_active_relationship: false, erasable: true, decision: "ANONYMIZE_NOW", is_bound: false,
    purgeable_now: false, governing_retention_until: null, bindings: [] };
  vi.spyOn(endpoints, "getDsgvoStatus").mockResolvedValue(status);
  vi.spyOn(endpoints, "getDsgvoExport").mockResolvedValueOnce({
    person: { email: "export-only@example.test" }, roles: { vendor_ids: [], tenant_profile_ids: [], owner_profile_ids: [] },
    processing: { anonymized_at: null, processing_restricted: false, deletion_requested_at: null }, retention_bindings: [],
  }).mockRejectedValueOnce(new Error("Export access expired"));
  render(<DsgvoPanel personId="one" canPrepare canExport />);
  fireEvent.click(await screen.findByRole("button", { name: "Daten anzeigen (Art. 15)" }));
  await screen.findByText("export-only@example.test");
  fireEvent.click(screen.getByRole("button", { name: "Daten anzeigen (Art. 15)" }));
  await screen.findByText("Error: Export access expired");
  expect(screen.queryByText("export-only@example.test")).not.toBeInTheDocument();
});
