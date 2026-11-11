import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AssetReportsPage } from "./AssetReportsPage";

const wegProperty = { id: "prop-weg", name: "WEG Musterhaus", management_type: "WEG" };
const mietProperty = { id: "prop-miet", name: "Mietshaus", management_type: "MIET" };
const run = { id: "run-1", property_id: "prop-weg", period_start: "2025-01-01", period_end: "2025-12-31", status: "draft" };

function mockLoad(runs: unknown[] = [run], properties: unknown[] = [wegProperty, mietProperty]) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/weg-settlements?run_type=VERMOEGENSBERICHT") return runs as never;
    if (path === "/api/properties") return properties as never;
    if (String(path).startsWith("/api/accounting-scopes/")) return { items: [], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/vermoegensberichte"]}>
      <Routes>
        <Route path="/vermoegensberichte" element={<AssetReportsPage />} />
        <Route path="/vermoegensberichte/:id" element={<p>Detailseite</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("creates an asset report for a WEG property and navigates to its detail", async () => {
  mockLoad();
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "run-new" } as never);
  renderPage();
  await screen.findByRole("link", { name: "Details" });
  expect(screen.getByText("WEG Musterhaus")).toBeVisible();
  expect(screen.getByText("Entwurf")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Vermögensbericht" }));
  const select = screen.getByRole("combobox", { name: "WEG-Objekt *" });
  expect(select).toHaveTextContent("WEG Musterhaus (WEG)");
  expect(select).not.toHaveTextContent("Mietshaus");
  fireEvent.change(select, { target: { value: "prop-weg" } });
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2025-01-01" } });
  fireEvent.change(screen.getByLabelText("Stichtag (bis) *"), { target: { value: "2025-12-31" } });
  fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

  expect(await screen.findByText("Detailseite")).toBeVisible();
  expect(post).toHaveBeenCalledWith("/api/weg-settlements/asset-reports", {
    property_id: "prop-weg", period_start: "2025-01-01", period_end: "2025-12-31", accounting_scope_id: null,
  });
});

it("shows the server error and stays on the page when creating fails", async () => {
  mockLoad([]);
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(400, "Der Zeitraum überschneidet sich mit einem bestehenden Bericht."));
  renderPage();
  await screen.findByText("Keine Vermögensberichte vorhanden.");

  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Vermögensbericht" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "prop-weg" } });
  fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));

  expect(await screen.findByText("Der Zeitraum überschneidet sich mit einem bestehenden Bericht.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Anlegen" })).toBeEnabled();
  expect(screen.queryByText("Detailseite")).not.toBeInTheDocument();
});
