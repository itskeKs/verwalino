import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DunningNoticeDetailPage } from "./DunningNoticeDetailPage";

const auth = vi.hoisted(() => ({ role: "OWNER_ADMIN", organization_id: "org" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: auth }) }));

const draft = {
  id: "notice", status: "draft", level: 1, issue_date: "2026-03-02", due_date: "2026-03-16",
  principal_amount: "700.00", fee_amount: "0.00", interest_amount: "0.00", total_amount: "700.00",
  included_items: [], interest_snapshot: null,
};

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url === "/api/dunning/notice") return draft;
    if (url.startsWith("/api/dunning")) return [];
    if (url === "/api/tenant-profiles") return [{ id: "tenant", person_id: "person" }];
    if (url === "/api/owner-profiles") return [{ id: "owner", person_id: "person" }];
    if (url === "/api/persons") return [{ id: "person", first_name: "Ada", last_name: "Test" }];
    if (url.startsWith("/api/leases")) return [{ id: "lease", tenant_profile_id: "tenant", unit_id: "unit", valid_from: "2026-01-01" }];
    if (url.startsWith("/api/units")) return [{ id: "unit", property_id: "property" }];
    if (url.startsWith("/api/properties")) return [{ id: "property", name: "Haus", management_type: "MIET" }];
    if (url.startsWith("/api/economic-plans")) return [];
    throw new Error(`Unexpected GET ${url}`);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function show() {
  return render(
    <MemoryRouter initialEntries={["/mahnungen/notice"]}>
      <Routes>
        <Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

it("requires an explicit account selection when several accounts are eligible", async () => {
  let attempts = 0;
  vi.spyOn(api, "post").mockImplementation(async (url: string) => {
    if (url !== "/api/dunning/notice/issue") throw new Error(`Unexpected POST ${url}`);
    attempts += 1;
    if (attempts === 1) {
      throw new ApiError(409,
        "Für das Objekt sind mehrere laufende Konten mit IBAN hinterlegt. Wählen Sie das Zielkonto aus.",
        "dunning_bank_selection_required", undefined, {
          code: "dunning_bank_selection_required",
          message: "Für das Objekt sind mehrere laufende Konten mit IBAN hinterlegt. Wählen Sie das Zielkonto aus.",
          bank_accounts: [
            { id: "a", label: "Mietkonto", iban: "IBAN-DE-A" },
            { id: "b", label: "Zweitkonto", iban: "IBAN-DE-B" },
          ],
        });
    }
    return { ...draft, status: "issued" };
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Mahnung ausgeben" }));
  const select = await screen.findByLabelText("Zielkonto für die Zahlung");
  expect(screen.getByRole("option", { name: /Mietkonto/ })).toBeTruthy();
  expect(screen.getByRole("option", { name: /Zweitkonto/ })).toBeTruthy();
  fireEvent.change(select, { target: { value: "b" } });
  fireEvent.click(screen.getByRole("button", { name: "Mahnung ausgeben" }));
  await waitFor(() =>
    expect(api.post).toHaveBeenLastCalledWith("/api/dunning/notice/issue", { bank_account_id: "b" }),
  );
  expect((await screen.findAllByText("Ausgestellt")).length).toBeGreaterThan(0);
});

it("shows only the guidance when the selection refusal carries no accounts", async () => {
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(409,
    "Für das Objekt sind mehrere laufende Konten mit IBAN hinterlegt. Wählen Sie das Zielkonto aus.",
    "dunning_bank_selection_required", undefined, {
      code: "dunning_bank_selection_required",
      message: "Für das Objekt sind mehrere laufende Konten mit IBAN hinterlegt. Wählen Sie das Zielkonto aus.",
      bank_accounts: [],
    }));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Mahnung ausgeben" }));
  expect(await screen.findByText(/mehrere laufende Konten/)).toBeVisible();
  expect(screen.queryByLabelText("Zielkonto für die Zahlung")).toBeNull();
});

it("shows the actionable guidance when no eligible account exists", async () => {
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(409,
    "Für das Objekt ist kein aktives laufendes Konto (LAUFEND) mit IBAN hinterlegt.",
    "dunning_bank_account_missing", undefined, {
      code: "dunning_bank_account_missing",
      message: "Für das Objekt ist kein aktives laufendes Konto (LAUFEND) mit IBAN hinterlegt.",
    }));
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Mahnung ausgeben" }));
  expect(await screen.findByText(/kein aktives laufendes Konto/)).toBeVisible();
  expect(screen.queryByLabelText("Zielkonto für die Zahlung")).toBeNull();
  expect(screen.getByRole("button", { name: "Mahnung ausgeben" })).toBeEnabled();
});
