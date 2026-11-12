import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AuthCtx } from "../auth/useAuth";
import type { LoginResponse, Property } from "../api/types";
import { EconomicPlansPage } from "./EconomicPlansPage";

afterEach(() => { cleanup(); sessionStorage.clear(); vi.restoreAllMocks(); });

const SESSION_A: LoginResponse = { user_id: "user-a", organization_id: "org-a", role: "ADMIN" };

function renderPage(session: LoginResponse = SESSION_A) {
  return render(
    <AuthCtx.Provider value={{ loading: false, session, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter><EconomicPlansPage /></MemoryRouter>
    </AuthCtx.Provider>,
  );
}

it("explicitly labels the WEG property select without including property options", async () => {
  const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [property] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  const select = screen.getByRole("combobox", { name: "WEG-Objekt *" });
  expect(select).toHaveAttribute("aria-label", "WEG-Objekt *");
  expect(select).toHaveAccessibleName("WEG-Objekt *");
  expect(select).toHaveTextContent("WEG Haus (WEG)");
  fireEvent.change(select, { target: { value: property.id } });
  expect(select).toHaveValue(property.id);
});

it("prefills property periods and refreshes them when the start year changes", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [{ id: "april", name: "Aprilhaus", management_type: "WEG", fiscal_year_start_month: 4 }] as never;
    if (path.includes("/settlement-periods")) {
      const year = Number(new URL(path, "http://local").searchParams.get("year"));
      return [{ year, label: `${year}/${year + 1}`, period_start: `${year}-04-01`, period_end: `${year + 1}-03-31` }] as never;
    }
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april" } });
  fireEvent.change(screen.getByLabelText("Wirtschaftsjahr *"), { target: { value: "2025" } });
  await screen.findByRole("option", { name: "2025/2026" });
  expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2025-04-01");
  expect(screen.getByLabelText("Zeitraum bis *")).toHaveValue("2026-03-31");
});

it("keeps dates that still fit the offered period when the property changes", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [
      { id: "april", name: "Aprilhaus", management_type: "WEG", fiscal_year_start_month: 4 },
      { id: "april2", name: "Zweithaus", management_type: "WEG", fiscal_year_start_month: 4 },
    ] as never;
    if (path.includes("/settlement-periods")) return [{ year: 2026, label: "2026/2027", period_start: "2026-04-01", period_end: "2027-03-31" }] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april" } });
  await waitFor(() => expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2026-04-01"));
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2026-06-01" } });
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april2" } });
  expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2026-06-01");
  expect(screen.getByLabelText("Zeitraum bis *")).toHaveValue("2027-03-31");
});

it("restores the form draft after leaving and returning to the page", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  const first = renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung (Jahr)"), { target: { value: "3000.00" } });
  first.unmount();
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("3000.00");
});

it("does not restore a draft written by another account", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  sessionStorage.setItem("economic-plan-form-draft", JSON.stringify({
    identity: "org-b:user-b",
    form: { property_id: "foreign-property", plan_year: 2026, period_start: "", period_end: "", reserve_contribution_total: "9999.00", monthly_due_day: 1 },
    showForm: true,
  }));
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  expect(screen.queryByLabelText("Rücklagenzuführung (Jahr)")).not.toBeInTheDocument();
});

it("keeps the amount and sets the offered period when the amount is typed before the object is chosen", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [{ id: "april", name: "Aprilhaus", management_type: "WEG", fiscal_year_start_month: 4 }] as never;
    if (path.includes("/settlement-periods")) return [{ year: 2026, label: "2026/2027", period_start: "2026-04-01", period_end: "2027-03-31" }] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung (Jahr)"), { target: { value: "3000.00" } });
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april" } });
  await waitFor(() => expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2026-04-01"));
  expect(screen.getByLabelText("Zeitraum bis *")).toHaveValue("2027-03-31");
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("3000.00");
});

it("resets the form when starting a new plan even with a restored draft", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  const first = renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung (Jahr)"), { target: { value: "3000.00" } });
  first.unmount();
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("3000.00");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("0.00");
});

it("discards the draft when the form is cancelled", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  const first = renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung (Jahr)"), { target: { value: "3000.00" } });
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  first.unmount();
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  expect(screen.queryByLabelText("Rücklagenzuführung (Jahr)")).not.toBeInTheDocument();
});

it.each([
  ["midyear scope", "2025-07-01", "2026-03-31"],
  ["gap-closing transition", "2025-01-01", "2025-03-31"],
])("submits editable offered dates for a %s", async (_case, start, end) => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [{ id: "april", name: "Aprilhaus", management_type: "WEG", fiscal_year_start_month: 4 }] as never;
    if (path.includes("/settlement-periods")) return [{ year: 2025, label: "2025/2026", period_start: "2025-04-01", period_end: "2026-03-31" }] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  vi.spyOn(api, "post").mockResolvedValue({ id: "new-plan" });
  renderPage();
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april" } });
  await waitFor(() => expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2025-04-01"));
  expect(screen.getByLabelText("Zeitraum von *")).not.toHaveAttribute("readonly");
  expect(screen.getByLabelText("Zeitraum bis *")).not.toHaveAttribute("readonly");
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: start } });
  fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: end } });
  expect(screen.getByRole("combobox", { name: "Abrechnungszeitraum" })).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/economic-plans", expect.objectContaining({
    property_id: "april", period_start: start, period_end: end,
  })));
});

it.each([
  [new ApiError(404, "Wirtschaftspläne konnten nicht geladen werden."), "Wirtschaftspläne konnten nicht geladen werden."],
  [new ApiError(401, "Unauthorized"), "Nicht authentifiziert."],
  [new Error("Synthetic network failure"), "Error: Synthetic network failure"],
])("renders load errors without the API class prefix and preserves auth and fallback: %s", async (error, expected) => {
  const get = vi.spyOn(api, "get").mockRejectedValue(error);
  try {
    renderPage();
    const rendered = await screen.findByText(expected, { exact: true });
    expect(rendered.firstChild?.textContent?.trim()).toBe(expected);
    expect(screen.queryByText(/ApiError:/)).not.toBeInTheDocument();
  } finally { get.mockRestore(); }
});
