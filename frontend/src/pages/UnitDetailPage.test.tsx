import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { UnitDetailPage } from "./UnitDetailPage";
import * as endpoints from "../api/endpoints";
import { AuthCtx } from "../auth/useAuth";
import type { LeaseRecord } from "../api/types";

vi.mock("../api/endpoints", async (importOriginal) => ({
  ...await importOriginal<typeof endpoints>(),
  getUnit: vi.fn(), getProperty: vi.fn(), listOwnership: vi.fn(), listLeases: vi.fn(),
  listOwnerProfiles: vi.fn(), listTenantProfiles: vi.fn(), listPersons: vi.fn(),
  createOwnership: vi.fn(), createLease: vi.fn(), patchLease: vi.fn(),
}));

const original: LeaseRecord = { id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01", valid_to: null, base_rent: "700.00", advance_payment: "150.00", deposit: null, notes: "Notiz" };
const march = { id: "change", lease_agreement_id: "lease", valid_from: "2026-03-15", base_rent: "725.00", advance_payment: "175.00", created_at: "2026-01-01T12:00:00Z" };
let history: typeof march[];
let failure: number | "network" | null;
let fetchMock: ReturnType<typeof vi.fn>;
function show(role = "OWNER_ADMIN") {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/objekte/property/einheiten/unit"]}>
      <Routes><Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} /></Routes>
    </MemoryRouter>
  </AuthCtx.Provider>);
}
async function openChange(index = 0) {
  const buttons = await screen.findAllByRole("button", { name: /^Miete ändern ab/ });
  await waitFor(() => expect(buttons[index]).toBeEnabled());
  fireEvent.click(buttons[index]);
  return screen.findByRole("dialog", { name: "Miete ändern" });
}
function date(value: string) { fireEvent.change(screen.getByLabelText("Gültig ab", { exact: true }), { target: { value } }); }
function money(label: string, value: string) { fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } }); }
function save() { fireEvent.click(screen.getByRole("button", { name: "Mietänderung speichern" })); }
function posts() { return fetchMock.mock.calls.filter(([, options]) => options?.method === "POST"); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  vi.resetAllMocks(); history = []; failure = null;
  fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      if (failure === "network") throw new TypeError("Netzwerk nicht erreichbar");
      if (failure) return new Response(JSON.stringify({ detail: "Serverprüfung fehlgeschlagen" }), { status: failure });
      const row = { ...march, ...JSON.parse(options.body as string), id: "new-change", lease_agreement_id: url.split("/")[3] };
      history.push(row); return new Response(JSON.stringify(row), { status: 201 });
    }
    return new Response(JSON.stringify(history), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.mocked(endpoints.getUnit).mockResolvedValue({ id: "unit", number: "1" } as never);
  vi.mocked(endpoints.getProperty).mockResolvedValue({ id: "property", name: "Testhaus" } as never);
  vi.mocked(endpoints.listOwnership).mockResolvedValue([]);
  vi.mocked(endpoints.listLeases).mockResolvedValue([{
    id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01",
    valid_to: null, base_rent: "500.00", advance_payment: "100.00", deposit: null, notes: "Notiz",
  }]);
  vi.mocked(endpoints.listOwnerProfiles).mockResolvedValue([]);
  vi.mocked(endpoints.listTenantProfiles).mockResolvedValue([]);
  vi.mocked(endpoints.listPersons).mockResolvedValue([]);
  vi.mocked(endpoints.patchLease).mockResolvedValue({} as never);
});

it("ends an existing lease without resending unchanged monetary fields", async () => {
  const user = userEvent.setup();
  show();
  await user.click(await screen.findByRole("button", { name: "Bearbeiten / beenden" }));
  expect(screen.getByRole("combobox", { name: /^Mieter \*$/ })).toHaveAccessibleName("Mieter *");
  expect(screen.getByLabelText("Vertragsbeginn *")).toBeDisabled();
  await user.type(screen.getByLabelText("Vertragsende"), "2025-12-31");
  await user.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.patchLease).toHaveBeenCalledWith("lease", {
    valid_to: "2025-12-31", notes: "Notiz",
  }));
  expect(endpoints.createLease).not.toHaveBeenCalled();
});

it("loads dated history with a clear original-contract source", async () => {
  history = [march]; show();
  expect(await screen.findByText("2026-03-15")).toBeVisible();
  expect(screen.getByText("Originalvertrag")).toBeVisible();
  expect(screen.getByText("Mietänderung")).toBeVisible();
  expect(screen.getByText("725.00")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith("/api/leases/lease/rent-changes", expect.anything());
});

it("posts April 750/150 exactly, keeps original lease money and persists on reload", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([original]);
  const view = show(); await openChange(); date("2026-04-01");
  expect(screen.getByLabelText("Vorauszahlung (EUR)", { exact: true })).toHaveValue("150.00");
  money("Kaltmiete (EUR)", "750,00"); save();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(posts()).toHaveLength(1);
  expect(posts()[0][0]).toBe("/api/leases/lease/rent-changes");
  expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2026-04-01", base_rent: "750.00", advance_payment: "150.00" });
  expect(original.base_rent).toBe("700.00"); expect(original.advance_payment).toBe("150.00");
  expect(endpoints.patchLease).not.toHaveBeenCalled(); expect(endpoints.createLease).not.toHaveBeenCalled();
  expect(await screen.findByText("2026-04-01")).toBeVisible();
  view.unmount(); show(); expect(await screen.findByText("2026-04-01")).toBeVisible();
});

it("prefills the full rate at the chosen date, preserving NK on a subsequent change", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([original]);
  history = [march, { ...march, id: "later", valid_from: "2026-06-01", base_rent: "800.00", advance_payment: "200.00" }];
  show(); await openChange(); date("2026-02-01");
  expect(screen.getByLabelText("Kaltmiete (EUR)", { exact: true })).toHaveValue("700.00");
  date("2026-04-01");
  expect(screen.getByLabelText("Kaltmiete (EUR)", { exact: true })).toHaveValue("725.00");
  expect(screen.getByLabelText("Vorauszahlung (EUR)", { exact: true })).toHaveValue("175.00");
  money("Kaltmiete (EUR)", "750"); save();
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body).advance_payment).toBe("175.00");
});

it.each([409, 422, 403, 404, "network"] as const)("preserves inputs and retries after POST failure %s", async status => {
  failure = status; show(); await openChange(); date("2026-04-01");
  money("Kaltmiete (EUR)", "750,25"); money("Vorauszahlung (EUR)", "175,10"); save();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible();
  expect(screen.getByLabelText("Kaltmiete (EUR)", { exact: true })).toHaveValue("750,25");
  expect(screen.getByLabelText("Vorauszahlung (EUR)", { exact: true })).toHaveValue("175,10");
  failure = null; save();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(posts()).toHaveLength(2);
});

it("loads history read-only without exposing the change action", async () => {
  history = [march]; show("READ_ONLY"); expect(await screen.findByText("2026-03-15")).toBeVisible();
  expect(screen.queryByRole("button", { name: /^Miete ändern ab/ })).not.toBeInTheDocument();
});

it.each(["", "-1", "1e3", "1.001", "NaN", "1.234,56", "1000000000000"])("rejects invalid money %s without POST", async value => {
  show(); await openChange(); date("2026-04-01"); money("Kaltmiete (EUR)", value); save();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it.each(["", "2024-12-31", "2026-05-01", "2026-02-30"])("rejects invalid or out-of-lease date %s", async value => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([{ ...original, valid_to: "2026-04-30" }]);
  show(); await openChange(); date(value); save();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it("accepts the inclusive lease end and a zero advance", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([{ ...original, valid_to: "2026-04-30" }]);
  show(); await openChange(); date("2026-04-30"); money("Vorauszahlung (EUR)", "0"); save();
  await waitFor(() => expect(posts()).toHaveLength(1)); expect(JSON.parse(posts()[0][1].body).advance_payment).toBe("0.00");
});

it("rejects an already recorded date without POST", async () => {
  history = [march]; show(); await openChange(); date(march.valid_from); save();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(/bereits/); expect(posts()).toHaveLength(0);
});

it("locks double submission synchronously until completion", async () => {
  show(); await openChange(); date("2026-04-01");
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  const form = screen.getByRole("button", { name: "Mietänderung speichern" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form); expect(posts()).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  finish(new Response(JSON.stringify({ ...march, valid_from: "2026-04-01" }), { status: 201 }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("couples the selected contract and its history exactly", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([original, { ...original, id: "lease-two", base_rent: "900.00", advance_payment: "250.00" }]);
  show(); await openChange(1); date("2026-04-01");
  expect(screen.getByLabelText("Vorauszahlung (EUR)", { exact: true })).toHaveValue("250.00"); save();
  await waitFor(() => expect(posts()).toHaveLength(1)); expect(posts()[0][0]).toBe("/api/leases/lease-two/rent-changes");
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/leases/lease/rent-changes")).toBe(true);
});

it("shows history loading/error, disables changes and retries GET", async () => {
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; })); show();
  expect(await screen.findByText("Lade Mietänderungen…")).toBeVisible();
  expect(screen.getByRole("button", { name: /^Miete ändern ab/ })).toBeDisabled();
  finish(new Response(JSON.stringify({ detail: "Verlauf nicht erreichbar" }), { status: 500 }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Verlauf nicht erreichbar");
  fireEvent.click(screen.getByRole("button", { name: "Mietänderungen erneut laden" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /^Miete ändern ab/ })).toBeEnabled());
});

it("cancels without mutation", async () => {
  show(); await openChange(); fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(posts()).toHaveLength(0);
});

it("never prefills another contract's history", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([original, { ...original, id: "lease-two", valid_from: "2026-01-01", base_rent: "900.00", advance_payment: "250.00" }]);
  const secondChange = { ...march, id: "second-change", lease_agreement_id: "lease-two", base_rent: "950.00", advance_payment: "275.00" };
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") return new Response(JSON.stringify({ ...secondChange, ...JSON.parse(options.body as string), id: "new-second-change" }), { status: 201 });
    return new Response(JSON.stringify(url.includes("lease-two") ? [secondChange] : [march]), { status: 200 });
  });
  show(); await openChange(1); date("2026-04-01");
  expect(screen.getByLabelText("Kaltmiete (EUR)", { exact: true })).toHaveValue("950.00");
  expect(screen.getByLabelText("Vorauszahlung (EUR)", { exact: true })).toHaveValue("275.00");
  save(); await waitFor(() => expect(posts()).toHaveLength(1));
  expect(posts()[0][0]).toBe("/api/leases/lease-two/rent-changes");
  expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2026-04-01", base_rent: "950.00", advance_payment: "275.00" });
});

it.each(["", "-0.01", "0.001", "Infinity"])("rejects invalid advance %s without POST", async value => {
  show(); await openChange(); date("2026-04-01"); money("Vorauszahlung (EUR)", value); save();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it("normalizes cent amounts exactly without floating-point rounding", async () => {
  show("CLERK"); await openChange(); date("2026-04-01");
  money("Kaltmiete (EUR)", "000750,29"); money("Vorauszahlung (EUR)", "0.01"); save();
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2026-04-01", base_rent: "750.29", advance_payment: "0.01" });
});

it("accepts the full Numeric(14,2) API bound without truncation", async () => {
  show(); await openChange(); date("2026-04-01");
  money("Kaltmiete (EUR)", "999999999999.99");
  money("Vorauszahlung (EUR)", "999999999999,99"); save();
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body)).toEqual({
    valid_from: "2026-04-01", base_rent: "999999999999.99", advance_payment: "999999999999.99",
  });
});

it("keeps a pending mutation's dialog open on Escape or close button", async () => {
  show(); await openChange(); date("2026-04-01");
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  save();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
  expect(screen.getByRole("dialog", { name: "Miete ändern" })).toBeVisible();
  expect(screen.getByLabelText("Gültig ab", { exact: true })).toBeDisabled();
  expect(posts()).toHaveLength(1);
  finish(new Response(JSON.stringify({ ...march, valid_from: "2026-04-01" }), { status: 201 }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
