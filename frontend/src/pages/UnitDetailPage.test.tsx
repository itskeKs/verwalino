import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { UnitDetailPage } from "./UnitDetailPage";
import * as endpoints from "../api/endpoints";
import { AuthCtx } from "../auth/useAuth";
import type { LeaseRecord, OwnershipRecord } from "../api/types";
import { api } from "../api/client";

vi.mock("../components/AttachmentsPanel", () => ({
  AttachmentsPanel: ({ propertyId, unitId, personId, canWrite }: { propertyId?: string; unitId?: string; personId?: string; canWrite: boolean }) =>
    <section aria-label="Dokumente" data-property={propertyId} data-unit={unitId} data-person={personId} data-can-write={String(canWrite)} />,
}));

vi.mock("../components/MeterPanel", () => ({
  MeterPanel: ({ propertyId, unitId, canWrite }: { propertyId: string; unitId?: string; canWrite: boolean }) =>
    <section aria-label="Zähler" data-property={propertyId} data-unit={unitId} data-can-write={String(canWrite)} />,
}));

vi.mock("../api/endpoints", async (importOriginal) => ({
  ...await importOriginal<typeof endpoints>(),
  getUnit: vi.fn(), getProperty: vi.fn(), listOwnership: vi.fn(), listLeases: vi.fn(),
  listOwnerProfiles: vi.fn(), listTenantProfiles: vi.fn(), listPersons: vi.fn(),
  createOwnership: vi.fn(), createLease: vi.fn(), patchLease: vi.fn(),
}));

const original: LeaseRecord = { id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01", valid_to: null, base_rent: "700.00", advance_payment: "150.00", deposit: null, notes: "Notiz" };
const march = { id: "change", lease_agreement_id: "lease", valid_from: "2026-03-15", base_rent: "725.00", advance_payment: "175.00", created_at: "2026-01-01T12:00:00Z" };
const staffelRows = [
  { ...march, id: "staffel-1", valid_from: "2027-01-31", base_rent: "750.00" },
  { ...march, id: "staffel-2", valid_from: "2027-02-28", base_rent: "775.00" },
  { ...march, id: "staffel-3", valid_from: "2027-03-31", base_rent: "800.00" },
];
const indexPreview = { valid_from: "2027-01-01", previous_valid_from: "2026-01-01", previous_base_rent: "600.00", base_rent: "627.27", advance_payment: "150.00" };
const indexInput = { old_index: "110", new_index: "115", declaration_received_on: "2026-11-15" };
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
      if (url.endsWith("/rent-changes/index/preview")) {
        return new Response(JSON.stringify(indexPreview), { status: 200 });
      }
      if (url.endsWith("/rent-changes/index")) {
        const row = { ...march, id: "index-change", valid_from: indexPreview.valid_from, base_rent: indexPreview.base_rent, advance_payment: indexPreview.advance_payment };
        history.push(row);
        return new Response(JSON.stringify(row), { status: 201 });
      }
      if (url.endsWith("/rent-changes/staffel")) {
        history.push(...staffelRows);
        return new Response(JSON.stringify(staffelRows), { status: 201 });
      }
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

it.each([['OWNER_ADMIN', true], ['CLERK', true], ['READ_ONLY', false], ['UNKNOWN', false]] as const)("binds the meter panel to the loaded unit and role %s", async (role, canWrite) => {
  show(role);
  const panel = await screen.findByRole("region", { name: "Zähler" });
  expect(panel).toHaveAttribute("data-property", "property");
  expect(panel).toHaveAttribute("data-unit", "unit");
  expect(panel).toHaveAttribute("data-can-write", String(canWrite));
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

const originalOwner: OwnershipRecord = {
  id: "ownership-old", unit_id: "unit", owner_profile_id: "seller", valid_from: "2026-01-01", valid_to: null, mea_share: "125.0000",
};
let ownerRows: OwnershipRecord[];
function ownershipSetup(rows: OwnershipRecord[] = [originalOwner]) {
  ownerRows = rows.map(row => ({ ...row }));
  vi.mocked(endpoints.listLeases).mockResolvedValue([]);
  vi.mocked(endpoints.listOwnership).mockImplementation(async () => ownerRows.map(row => ({ ...row })));
  vi.mocked(endpoints.listOwnerProfiles).mockResolvedValue([
    { id: "seller", person_id: "person-seller", notes: "" }, { id: "buyer", person_id: "person-buyer", notes: "" },
  ]);
  vi.mocked(endpoints.listPersons).mockResolvedValue([
    { id: "person-seller", first_name: "Anna", last_name: "Alt" }, { id: "person-buyer", first_name: "Berta", last_name: "Neu" },
  ] as never);
  vi.mocked(endpoints.createOwnership).mockImplementation(body => api.post("/api/ownership", body));
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (failure === "network") throw new TypeError("Netzwerk nicht erreichbar");
    if (failure) return new Response(JSON.stringify({ detail: "Eigentumsprüfung fehlgeschlagen" }), { status: failure });
    if (options?.method === "PATCH") {
      const id = url.split("/").at(-1);
      const row = { ...ownerRows.find(row => row.id === id)!, ...JSON.parse(options.body as string) };
      ownerRows = ownerRows.map(previous => previous.id === id ? row : previous);
      return new Response(JSON.stringify(row), { status: 200 });
    }
    const row = { ...JSON.parse(options?.body as string), id: "ownership-new", valid_to: null };
    ownerRows.push(row); return new Response(JSON.stringify(row), { status: 201 });
  });
}
function ownershipPatches() { return fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH"); }
async function openOwnershipEnd(index = 0) {
  fireEvent.click((await screen.findAllByRole("button", { name: "Eigentum beenden" }))[index]);
  return screen.findByRole("dialog", { name: "Eigentum beenden" });
}
function ownershipDate(value: string) { fireEvent.change(screen.getByLabelText("Letzter Eigentumstag", { exact: true }), { target: { value } }); }
function endOwnership() { fireEvent.click(within(screen.getByRole("dialog", { name: "Eigentum beenden" })).getByRole("button", { name: "Eigentum beenden" })); }

it("T26 exposes the exact ownership-end PATCH endpoint and inclusive date-only body", async () => {
  ownershipSetup(); expect(endpoints.patchOwnership).toBeTypeOf("function");
  expect(await endpoints.patchOwnership(originalOwner.id, { valid_to: "2026-06-30" })).toEqual({ ...originalOwner, valid_to: "2026-06-30" });
  expect(fetchMock).toHaveBeenCalledWith("/api/ownership/ownership-old", expect.objectContaining({ method: "PATCH", credentials: "include", body: JSON.stringify({ valid_to: "2026-06-30" }) }));
});

it("T26 ends June 30, preserves source data, persists and requires an explicit buyer POST from July 1", async () => {
  ownershipSetup(); const view = show(); const dialog = await openOwnershipEnd();
  expect(within(dialog).getByText(/Anna Alt/)).toBeVisible();
  expect(screen.getByLabelText("Letzter Eigentumstag", { exact: true })).toBeRequired();
  expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument();
  ownershipDate("2026-06-30"); endOwnership();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(ownershipPatches()).toHaveLength(1); expect(JSON.parse(ownershipPatches()[0][1].body)).toEqual({ valid_to: "2026-06-30" });
  expect(posts()).toHaveLength(0); expect(screen.getByRole("status")).toHaveTextContent(/2026-06-30/);
  const row = screen.getByRole("row", { name: /Anna Alt/ });
  expect(row).toHaveTextContent("2026-01-01"); expect(row).toHaveTextContent("2026-06-30"); expect(row).toHaveTextContent("125.0000");
  expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Eigentuemer *" })).toHaveValue("");
  expect(screen.getByLabelText("Gueltig ab *", { exact: true })).toHaveValue("2026-07-01");
  expect(screen.getByLabelText("Gueltig bis", { exact: true })).toHaveValue("");
  expect(screen.getByLabelText("MEA-Anteil", { exact: true })).toHaveValue("125.0000");
  fireEvent.click(screen.getByRole("button", { name: "Speichern" })); expect(posts()).toHaveLength(0);
  fireEvent.change(screen.getByRole("combobox", { name: "Eigentuemer *" }), { target: { value: "buyer" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(posts()).toHaveLength(1)); expect(posts()[0][0]).toBe("/api/ownership");
  expect(JSON.parse(posts()[0][1].body)).toEqual({ unit_id: "unit", owner_profile_id: "buyer", valid_from: "2026-07-01", mea_share: "125.0000" });
  await screen.findByRole("row", { name: /Berta Neu/ }); view.unmount(); show();
  expect(await screen.findByRole("row", { name: /Anna Alt/ })).toHaveTextContent("2026-06-30");
  expect(screen.getByRole("row", { name: /Berta Neu/ })).toHaveTextContent("2026-07-01");
  expect(endpoints.patchLease).not.toHaveBeenCalled(); expect(endpoints.createLease).not.toHaveBeenCalled();
});

it.each(["READ_ONLY", "UNKNOWN", "", "OWNER"])("T26 keeps ownership read-only for role %s", async role => {
  ownershipSetup(); show(role); expect(await screen.findByRole("row", { name: /Anna Alt/ })).toHaveTextContent("offen");
  expect(screen.queryByRole("button", { name: "Eigentum beenden" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Eigentum" })).not.toBeInTheDocument(); expect(ownershipPatches()).toHaveLength(0); expect(posts()).toHaveLength(0);
});
it.each(["", "2025-12-31", "2026-02-30", "9999-12-31"])("T26 rejects missing, invalid, early or overflowing end %s", async value => {
  ownershipSetup(); show(); await openOwnershipEnd(); ownershipDate(value); endOwnership();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible(); expect(ownershipPatches()).toHaveLength(0); expect(posts()).toHaveLength(0);
});
it.each([["2026-01-01", "2026-01-02"], ["2026-02-28", "2026-03-01"], ["2026-12-31", "2027-01-01"], ["2028-02-29", "2028-03-01"]])("T26 computes the calendar day after %s as %s without today rules", async (lastDay, nextDay) => {
  ownershipSetup(); show("CLERK"); await openOwnershipEnd(); ownershipDate(lastDay); endOwnership();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(JSON.parse(ownershipPatches()[0][1].body)).toEqual({ valid_to: lastDay }); expect(screen.getByLabelText("Gueltig ab *", { exact: true })).toHaveValue(nextDay); expect(posts()).toHaveLength(0);
});
it.each([403, 404, 409, 422, "network"] as const)("T26 preserves inputs after PATCH failure %s and retries", async status => {
  ownershipSetup(); failure = status; show(); await openOwnershipEnd(); ownershipDate("2026-06-30"); endOwnership();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible();
  expect(screen.getByLabelText("Letzter Eigentumstag", { exact: true })).toHaveValue("2026-06-30"); expect(ownerRows).toEqual([originalOwner]); expect(posts()).toHaveLength(0);
  expect(screen.queryByRole("heading", { name: "Neues Eigentum anlegen" })).not.toBeInTheDocument();
  failure = null; endOwnership(); await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(ownershipPatches()).toHaveLength(2);
  expect(screen.getByLabelText("Gueltig ab *", { exact: true })).toHaveValue("2026-07-01");
});
it("T26 scopes actions and MEA to the selected open row among several owners", async () => {
  ownershipSetup([originalOwner, { ...originalOwner, id: "ownership-second", owner_profile_id: "buyer", valid_from: "2026-03-01", mea_share: "75.0000" }, { ...originalOwner, id: "ownership-ended", valid_from: "2025-01-01", valid_to: "2025-12-31" }]);
  show(); await screen.findByRole("row", { name: /Berta Neu/ }); expect(screen.getAllByRole("button", { name: "Eigentum beenden" })).toHaveLength(2);
  expect(within(await openOwnershipEnd(1)).getByText(/Berta Neu/)).toBeVisible(); ownershipDate("2026-06-30"); endOwnership();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(ownershipPatches()[0][0]).toBe("/api/ownership/ownership-second"); expect(ownerRows[0]).toEqual(originalOwner);
  expect(screen.getByLabelText("MEA-Anteil", { exact: true })).toHaveValue("75.0000"); expect(screen.getByRole("combobox", { name: "Eigentuemer *" })).toHaveValue("");
});
it("T26 leaves source-less MEA empty", async () => {
  ownershipSetup([{ ...originalOwner, mea_share: null }]); show(); await openOwnershipEnd(); ownershipDate("2026-06-30"); endOwnership();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(screen.getByLabelText("MEA-Anteil", { exact: true })).toHaveValue("");
});
it("T26 synchronously blocks duplicate submits, inputs, cancel, Escape and close while pending", async () => {
  ownershipSetup(); show(); await openOwnershipEnd(); ownershipDate("2026-06-30"); let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; })); const dialog = screen.getByRole("dialog", { name: "Eigentum beenden" });
  const form = within(dialog).getByRole("button", { name: "Eigentum beenden" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form); expect(ownershipPatches()).toHaveLength(1);
  expect(screen.getByLabelText("Letzter Eigentumstag", { exact: true })).toBeDisabled(); expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Eigentum beenden" })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: "Escape" }); fireEvent.click(within(dialog).getByRole("button", { name: "Schließen" }));
  expect(screen.getByRole("dialog", { name: "Eigentum beenden" })).toBeVisible(); expect(posts()).toHaveLength(0);
  finish(new Response(JSON.stringify({ ...originalOwner, valid_to: "2026-06-30" }), { status: 200 }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(screen.getByRole("row", { name: /Anna Alt/ })).toHaveTextContent("2026-06-30");
});
it("T26 cancels without PATCH and starts another row with clean date/error", async () => {
  ownershipSetup([originalOwner, { ...originalOwner, id: "ownership-second", owner_profile_id: "buyer" }]); show(); await openOwnershipEnd(); endOwnership();
  expect(await within(screen.getByRole("dialog")).findByRole("alert")).toBeVisible(); ownershipDate("2026-06-30"); fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Abbrechen" })); expect(ownershipPatches()).toHaveLength(0);
  await openOwnershipEnd(1); expect(screen.getByLabelText("Letzter Eigentumstag", { exact: true })).toHaveValue(""); expect(within(screen.getByRole("dialog")).queryByRole("alert")).not.toBeInTheDocument();
});

it("T26 preserves the next-day proposal through cancel and explicit + Eigentum without selecting a buyer", async () => {
  ownershipSetup(); show(); await openOwnershipEnd(); ownershipDate("2026-06-30"); endOwnership();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  fireEvent.click(screen.getByRole("button", { name: "+ Eigentum" }));
  expect(screen.getByLabelText("Gueltig ab *", { exact: true })).toHaveValue("2026-07-01");
  expect(screen.getByRole("combobox", { name: "Eigentuemer *" })).toHaveValue("");
  expect(screen.getByLabelText("MEA-Anteil", { exact: true })).toHaveValue("125.0000");
  expect(posts()).toHaveLength(0);
});

it.each([["OWNER_ADMIN", true], ["CLERK", true], ["READ_ONLY", false], ["UNKNOWN", false]] as const)("T37 binds loaded unit document IDs for %s", async (role, canWrite) => {
  vi.mocked(endpoints.getUnit).mockResolvedValue({ id: "loaded-unit", property_id: "property", number: "1" } as never);
  show(role); const panel = await screen.findByRole("region", { name: "Dokumente" });
  expect(panel).toHaveAttribute("data-property", "property"); expect(panel).toHaveAttribute("data-unit", "loaded-unit"); expect(panel).toHaveAttribute("data-can-write", String(canWrite));
});

it("T37 ignores a late unit response after changing the route", async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof endpoints.getUnit>>) => void;
  vi.mocked(endpoints.getUnit).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValue({ id: "new-unit", number: "2" } as never);
  render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/objekte/property/einheiten/unit"]}>
    <Link to="/objekte/property/einheiten/new-unit">Andere Einheit</Link><Routes><Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} /></Routes>
  </MemoryRouter></AuthCtx.Provider>);
  fireEvent.click(screen.getByRole("link", { name: "Andere Einheit" }));
  const panel = await screen.findByRole("region", { name: "Dokumente" }); expect(panel).toHaveAttribute("data-unit", "new-unit");
  await act(async () => resolveOld({ id: "unit", number: "1" } as never));
  expect(screen.getByRole("region", { name: "Dokumente" })).toHaveAttribute("data-unit", "new-unit");
});

it.each(["CLERK", "OWNER_ADMIN", "READ_ONLY", "UNKNOWN"])("T42 offers task context creation only to writers (%s), with loaded IDs", async role => {
  vi.mocked(endpoints.getUnit).mockResolvedValue({ id: "loaded/u&1", property_id: "loaded/p&1", number: "42" } as never);
  vi.mocked(endpoints.getProperty).mockResolvedValue({ id: "loaded/p&1", name: "Loaded" } as never);
  show(role); await screen.findByRole("heading", { name: "Einheit 42" });
  const link = screen.queryByRole("link", { name: "Aufgabe anlegen" });
  if (role === "CLERK" || role === "OWNER_ADMIN") {
    expect(link).toHaveAttribute("href", "/aufgaben?property_id=loaded%2Fp%261&unit_id=loaded%2Fu%261&create=1");
  } else expect(link).toBeNull();
});

async function openStaffel() {
  const button = await screen.findByRole("button", { name: "Staffelmiete anlegen" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  return screen.findByRole("dialog", { name: "Staffelmiete anlegen" });
}
function fillStaffel() {
  money("Erste Erhöhung ab", "2027-01-31");
  money("Erhöhung (EUR)", "25,00");
  money("Intervall (Monate)", "12");
  money("Anzahl Erhöhungen", "3");
}
function saveStaffel() { fireEvent.click(screen.getByRole("button", { name: "Staffelmiete speichern" })); }

it.each(["OWNER_ADMIN", "CLERK"])("T43 posts fixed EUR staffel without start-rent metadata and preserves history (%s)", async role => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([original]);
  history = [march];
  const view = show(role); await openStaffel(); fillStaffel(); saveStaffel();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(posts()).toHaveLength(1);
  expect(posts()[0][0]).toBe("/api/leases/lease/rent-changes/staffel");
  expect(JSON.parse(posts()[0][1].body)).toEqual({
    staffel_start_date: "2027-01-31", staffel_increase_amount: "25.00", staffel_interval_months: 12, staffel_steps: 3,
  });
  const table = screen.getByRole("table", { name: /^Miethistorie:/ });
  expect(within(table).getByText("700.00")).toBeVisible();
  expect(within(table).getByText("725.00")).toBeVisible();
  for (const row of staffelRows) {
    const rendered = within(table).getByText(row.valid_from).closest("tr")!;
    expect(within(rendered).getByText(row.base_rent)).toBeVisible();
    expect(within(rendered).getByText("175.00")).toBeVisible();
    expect(within(rendered).queryByRole("button")).toBeNull();
  }
  expect(endpoints.patchLease).not.toHaveBeenCalled(); expect(endpoints.createLease).not.toHaveBeenCalled();
  view.unmount(); show(role); expect(await screen.findByText("2027-03-31")).toBeVisible();
});

it.each([["5", "0.05"], ["2,75", "0.0275"], ["0.00000000000000000001", "0.0000000000000000000001"]])("T43 sends percentage %s as an exact decimal fraction", async (input, fraction) => {
  show(); await openStaffel(); fillStaffel();
  fireEvent.change(screen.getByLabelText("Erhöhungsart"), { target: { value: "percent" } });
  money("Erhöhung (%)", input); saveStaffel();
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body)).toEqual({
    staffel_start_date: "2027-01-31", staffel_increase_percent: fraction, staffel_interval_months: 12, staffel_steps: 3,
  });
});

it.each(["READ_ONLY", "UNKNOWN"])("T43 does not offer staffel mutation to %s", async role => {
  show(role); await screen.findByText("Originalvertrag");
  expect(screen.queryByRole("button", { name: "Staffelmiete anlegen" })).toBeNull();
});

it.each([
  ["Erste Erhöhung ab", ""], ["Erste Erhöhung ab", "2024-12-31"],
  ["Erhöhung (EUR)", "0"], ["Erhöhung (EUR)", "-1"], ["Erhöhung (EUR)", "1.001"],
  ["Intervall (Monate)", "0"], ["Intervall (Monate)", "11"], ["Intervall (Monate)", "121"], ["Intervall (Monate)", "1.5"],
  ["Anzahl Erhöhungen", "0"], ["Anzahl Erhöhungen", "51"], ["Anzahl Erhöhungen", "2.5"],
])("T43 validates %s=%s without posting", async (label, value) => {
  show(); const dialog = await openStaffel(); fillStaffel(); money(label, value); saveStaffel();
  expect(await within(dialog).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it("T43 respects inclusive lease end and rejects dates after it", async () => {
  vi.mocked(endpoints.listLeases).mockResolvedValue([{ ...original, valid_to: "2027-01-31" }]);
  show(); const dialog = await openStaffel(); fillStaffel();
  money("Erste Erhöhung ab", "2027-02-01"); saveStaffel();
  expect(await within(dialog).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
  money("Erste Erhöhung ab", "2027-01-31"); money("Anzahl Erhöhungen", "1"); saveStaffel();
  await waitFor(() => expect(posts()).toHaveLength(1));
});

it.each([409, 422, 403, 404, "network"] as const)("T43 retains inputs and permits retry after %s", async status => {
  failure = status; show(); const dialog = await openStaffel(); fillStaffel(); saveStaffel();
  expect(await within(dialog).findByRole("alert")).toBeVisible();
  expect(screen.getByLabelText("Erhöhung (EUR)")).toHaveValue("25,00");
  expect(screen.getByLabelText("Erste Erhöhung ab")).toHaveValue("2027-01-31");
  failure = null; saveStaffel();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument()); expect(posts()).toHaveLength(2);
});

it("T43 prevents repeated submits and closing while staffel is saving", async () => {
  show(); const dialog = await openStaffel(); fillStaffel();
  let resolvePost!: (value: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { resolvePost = resolve; }));
  const form = dialog.querySelector("form")!;
  fireEvent.submit(form); fireEvent.submit(form); expect(posts()).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Staffelmiete speichern" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: "Escape" }); expect(dialog).toBeVisible();
  await act(async () => resolvePost(new Response(JSON.stringify(staffelRows), { status: 201 })));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("T43 disables staffel when history failed to load and enables it after retry", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Historie nicht verfügbar" }), { status: 500 }));
  show(); await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Staffelmiete anlegen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Mietänderungen erneut laden" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Staffelmiete anlegen" })).toBeEnabled());
});

it.each(["", "0", "0,000", "-5", "1e2", "Infinity", "NaN", "1,2.3"])("T43 rejects invalid percentage %s", async value => {
  show(); const dialog = await openStaffel(); fillStaffel();
  fireEvent.change(screen.getByLabelText("Erhöhungsart"), { target: { value: "percent" } });
  money("Erhöhung (%)", value); saveStaffel();
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("positive Erhöhung in Prozent");
  expect(posts()).toHaveLength(0);
});

it("T43 switches back to EUR without sending the hidden percentage and accepts upper integer bounds", async () => {
  show(); await openStaffel(); fillStaffel();
  fireEvent.change(screen.getByLabelText("Erhöhungsart"), { target: { value: "percent" } });
  money("Erhöhung (%)", "5");
  fireEvent.change(screen.getByLabelText("Erhöhungsart"), { target: { value: "amount" } });
  money("Erhöhung (EUR)", "00025,29"); money("Intervall (Monate)", "120"); money("Anzahl Erhöhungen", "50");
  saveStaffel(); await waitFor(() => expect(posts()).toHaveLength(1));
  expect(JSON.parse(posts()[0][1].body)).toEqual({
    staffel_start_date: "2027-01-31", staffel_increase_amount: "25.29", staffel_interval_months: 120, staffel_steps: 50,
  });
});

it("T43 disables staffel until history has loaded and cancels without a mutation", async () => {
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  show(); await screen.findByText("Lade Mietänderungen…");
  expect(screen.getByRole("button", { name: "Staffelmiete anlegen" })).toBeDisabled();
  await act(async () => finish(new Response(JSON.stringify([]), { status: 200 })));
  await openStaffel(); fillStaffel(); fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(screen.queryByRole("dialog")).toBeNull(); expect(posts()).toHaveLength(0);
});

it("T43 checks current role again when an already-open dialog is submitted", async () => {
  function App({ role }: { role: string }) {
    return <AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter initialEntries={["/objekte/property/einheiten/unit"]}><Routes>
        <Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} />
      </Routes></MemoryRouter>
    </AuthCtx.Provider>;
  }
  const view = render(<App role="CLERK" />); await openStaffel(); fillStaffel();
  view.rerender(<App role="READ_ONLY" />); saveStaffel();
  expect(posts()).toHaveLength(0);
});

async function openIndex() {
  const button = await screen.findByRole("button", { name: "Indexmiete berechnen" });
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button);
  return screen.findByRole("dialog", { name: "Indexmiete berechnen" });
}
function fillIndex() {
  money("VPI alt", "110"); money("VPI neu", "115"); money("Zugang der Erklärung", "2026-11-15");
}
function calculateIndex() { fireEvent.click(screen.getByRole("button", { name: "Berechnen" })); }
async function previewIndex() {
  calculateIndex(); return screen.findByRole("button", { name: "Als Mietänderung übernehmen" });
}

it.each(["OWNER_ADMIN", "CLERK"])("T44 previews nonmutating and explicitly adopts exact server amounts (%s)", async role => {
  const lease = { ...original, valid_from: "2026-01-01", base_rent: "600.00" };
  vi.mocked(endpoints.listLeases).mockResolvedValue([lease]);
  const view = show(role); const dialog = await openIndex(); fillIndex();
  expect(within(dialog).getByText(/Destatis/)).toHaveTextContent(/gleiche.*Basis/);
  expect(within(dialog).getByText(/schriftliche Indexmietvereinbarung/)).toHaveTextContent(/Textform/);
  expect(within(dialog).getByText(/kein.*Nachweis/)).toBeVisible();
  const adopt = await previewIndex();
  expect(posts()).toHaveLength(1); expect(posts()[0][0]).toBe("/api/leases/lease/rent-changes/index/preview");
  expect(JSON.parse(posts()[0][1].body)).toEqual(indexInput); expect(history).toEqual([]);
  expect(within(dialog).getByText(/600.00/)).toBeVisible(); expect(within(dialog).getByText(/627.27/)).toBeVisible();
  expect(within(dialog).getByText(/2027-01-01/)).toBeVisible(); fireEvent.click(adopt);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posts()).toHaveLength(2); expect(posts()[1][0]).toBe("/api/leases/lease/rent-changes/index");
  expect(JSON.parse(posts()[1][1].body)).toEqual({ ...indexInput, expected_base_rent: "627.27", expected_advance_payment: "150.00", expected_previous_valid_from: "2026-01-01" });
  const table = screen.getByRole("table", { name: /^Miethistorie:/ });
  expect(within(table).getByText("600.00")).toBeVisible(); expect(within(table).getByText("627.27")).toBeVisible();
  expect(within(table).getAllByText("150.00")).toHaveLength(2); expect(lease.base_rent).toBe("600.00");
  expect(endpoints.patchLease).not.toHaveBeenCalled(); expect(endpoints.createLease).not.toHaveBeenCalled();
  view.unmount(); show(role); expect(await screen.findByText("2027-01-01")).toBeVisible();
});

it.each(["VPI alt", "VPI neu", "Zugang der Erklärung"])("T44 invalidates preview when %s changes", async label => {
  show(); await openIndex(); fillIndex(); await previewIndex(); money(label, label === "Zugang der Erklärung" ? "2026-12-01" : "116");
  expect(screen.queryByRole("button", { name: "Als Mietänderung übernehmen" })).toBeNull(); expect(posts()).toHaveLength(1);
});

it.each(["", "0", "0,000000", "-110", "NaN", "Infinity", "1e2", "110.1234567", "1,2.3"])("T44 rejects invalid index %s without requests", async value => {
  show(); const dialog = await openIndex(); fillIndex(); money("VPI alt", value); calculateIndex();
  expect(await within(dialog).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it("T44 rejects equal normalized indices and missing receipt date", async () => {
  show(); const dialog = await openIndex(); fillIndex(); money("VPI alt", "00115,000000"); calculateIndex();
  expect(await within(dialog).findByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
  money("VPI alt", "110"); money("Zugang der Erklärung", ""); calculateIndex();
  expect(within(dialog).getByRole("alert")).toBeVisible(); expect(posts()).toHaveLength(0);
});

it("T44 allows decreases and sends six decimal indices without monetary arithmetic", async () => {
  show(); await openIndex(); fillIndex(); money("VPI alt", "00115,123456"); money("VPI neu", "110,123456"); await previewIndex();
  expect(JSON.parse(posts()[0][1].body)).toEqual({ ...indexInput, old_index: "115.123456", new_index: "110.123456" });
});

it.each(["READ_ONLY", "UNKNOWN"])("T44 hides index mutation from %s", async role => {
  show(role); await screen.findByText("Originalvertrag"); expect(screen.queryByRole("button", { name: "Indexmiete berechnen" })).toBeNull();
});

it.each([409, 422, 403, 404, "network"] as const)("T44 preserves inputs after preview error %s and allows recalculation", async status => {
  show(); const dialog = await openIndex(); fillIndex(); failure = status; calculateIndex();
  expect(await within(dialog).findByRole("alert")).toBeVisible(); expect(screen.getByLabelText("VPI alt")).toHaveValue("110");
  expect(screen.queryByRole("button", { name: "Als Mietänderung übernehmen" })).toBeNull();
  failure = null; await previewIndex(); expect(posts()).toHaveLength(2);
});

it("T44 discards stale preview on adoption conflict and requires recalculation", async () => {
  show(); const dialog = await openIndex(); fillIndex(); const adopt = await previewIndex(); failure = 409; fireEvent.click(adopt);
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(/erneut berechnen/);
  expect(screen.queryByRole("button", { name: "Als Mietänderung übernehmen" })).toBeNull(); expect(history).toEqual([]);
  failure = null; fireEvent.click(await previewIndex());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull()); expect(posts()).toHaveLength(4);
});

it("T44 locks preview and adoption against duplicate submits and closing", async () => {
  show(); const dialog = await openIndex(); fillIndex(); let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  fireEvent.submit(dialog.querySelector("form")!); fireEvent.submit(dialog.querySelector("form")!); expect(posts()).toHaveLength(1);
  expect(screen.getByLabelText("VPI alt")).toBeDisabled(); expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: "Escape" }); expect(dialog).toBeVisible();
  await act(async () => finish(new Response(JSON.stringify(indexPreview), { status: 200 })));
  const adopt = await screen.findByRole("button", { name: "Als Mietänderung übernehmen" });
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  fireEvent.click(adopt); fireEvent.click(adopt); expect(posts()).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Berechnen" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  await act(async () => finish(new Response(JSON.stringify({ ...march, id: "index-change", ...indexPreview }), { status: 201 })));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("T44 disables index action until history loads, supports cancellation and resets reopening", async () => {
  let finish!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
  show(); await screen.findByText("Lade Mietänderungen…"); expect(screen.getByRole("button", { name: "Indexmiete berechnen" })).toBeDisabled();
  await act(async () => finish(new Response(JSON.stringify([]), { status: 200 })));
  await openIndex(); fillIndex(); await previewIndex(); fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(history).toEqual([]); await openIndex(); expect(screen.getByLabelText("VPI alt")).toHaveValue("");
  expect(screen.queryByRole("button", { name: "Als Mietänderung übernehmen" })).toBeNull();
});

it("T44 disables index action on history load errors until retry", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Historie nicht verfügbar" }), { status: 500 }));
  show(); await screen.findByRole("alert"); expect(screen.getByRole("button", { name: "Indexmiete berechnen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Mietänderungen erneut laden" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Indexmiete berechnen" })).toBeEnabled());
});
