import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import type { Mock, MockInstance } from "vitest";
import { AuthCtx } from "../auth/useAuth";
import { PaymentsPage } from "./PaymentsPage";

const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  const fixtures: Record<string, unknown[]> = {
    "/api/payments": [],
    "/api/bank-accounts": [{ id: "deposit-account", property_id: "property", label: "Kautionskonto", account_type: "KAUTION" }],
    "/api/properties": [{ id: "property", name: "Testhaus" }],
    "/api/leases": [{ id: "lease", tenant_profile_id: "tenant", unit_id: "unit", valid_from: "2026-01-01", valid_to: null }],
    "/api/tenant-profiles": [{ id: "tenant", person_id: "person" }],
    "/api/owner-profiles": [{ id: "owner", person_id: "person" }],
    "/api/persons": [{ id: "person", first_name: "Ada", last_name: "Test" }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, options?: RequestInit) => {
    if (options?.method && options.method !== "GET") throw new Error("No mutation expected");
    if (input in fixtures) return json(fixtures[input]);
    throw new Error(`Unexpected URL: ${input}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openCreate() {
  render(<MemoryRouter><PaymentsPage /></MemoryRouter>);
  await screen.findByText("Keine Zahlungen vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Zahlung" }));
}
function expectExplicitSelect(labelText: string) {
  const label = Array.from(document.querySelectorAll("label")).find(item => item.firstChild?.textContent?.trim() === labelText);
  const select = label?.querySelector("select");
  expect(select).toHaveAttribute("aria-label", labelText);
  expect(screen.getByLabelText(labelText, { exact: true })).toBe(select);
  expect(select).toHaveAccessibleName(labelText);
  return select!;
}

it.each(["Bankkonto *", "Zahlungstyp *", "Richtung *", "Mieter", "Mietvertrag"])("explicitly names %s for exact browser label lookup", async label => {
  await openCreate();
  expectExplicitSelect(label);
});
it("explicitly names Eigentuemer after switching to an owner payment type", async () => {
  await openCreate();
  const type = Array.from(document.querySelectorAll("label")).find(item => item.firstChild?.textContent?.trim() === "Zahlungstyp *")!.querySelector("select")!;
  fireEvent.change(type, { target: { value: "HAUSGELD_VORSCHUSS" } });
  expectExplicitSelect("Eigentuemer");
});
it('offers a reserve withdrawal without an owner or tenant counterparty', async () => {
  await openCreate();
  fireEvent.change(expectExplicitSelect('Zahlungstyp *'), { target: { value: 'RUECKLAGENENTNAHME' } });
  expect(screen.getByLabelText('Zahlungstyp *', { exact: true })).toHaveValue('RUECKLAGENENTNAHME');
  expect(await screen.findByLabelText('Bestätigter Kostenbeleg *')).toBeInTheDocument();
  expect(screen.getByLabelText('Beschluss-Sammlung *')).toBeInTheDocument();
  expect(screen.queryByLabelText('Eigentuemer', { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Mieter', { exact: true })).not.toBeInTheDocument();
});
it('shows the German app refusal when a manual reserve payout has no evidence', async () => {
  const fetchMock = vi.mocked(fetch);
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url, init) => String(url).includes('/cost-entries') || String(url).includes('/meetings/register/')
    ? Promise.resolve(json([])) : original(url, init));
  await openCreate();
  fireEvent.change(screen.getByLabelText('Bankkonto *', { exact: true }), { target: { value: 'deposit-account' } });
  fireEvent.change(screen.getByLabelText('Zahlungstyp *', { exact: true }), { target: { value: 'RUECKLAGENENTNAHME' } });
  fireEvent.change(screen.getByLabelText('Betrag *', { exact: true }), { target: { value: '1200.00' } });
  fireEvent.change(screen.getByLabelText('Wertstellungsdatum *', { exact: true }), { target: { value: '2025-02-01' } });
  await waitFor(() => expect(screen.getByLabelText('Bestätigter Kostenbeleg *')).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: 'Anlegen' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.');
  expect(screen.getByLabelText('Betrag *', { exact: true })).toHaveValue('1200.00');
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});
it.each(["IN", "OUT"])("supports KAUTION %s through the existing payment fields", async direction => {
  await openCreate();
  fireEvent.change(expectExplicitSelect("Bankkonto *"), { target: { value: "deposit-account" } });
  fireEvent.change(expectExplicitSelect("Zahlungstyp *"), { target: { value: "KAUTION" } });
  fireEvent.change(expectExplicitSelect("Richtung *"), { target: { value: direction } });
  fireEvent.change(expectExplicitSelect("Mieter"), { target: { value: "tenant" } });
  fireEvent.change(expectExplicitSelect("Mietvertrag"), { target: { value: "lease" } });
  expect(screen.getByLabelText("Bankkonto *", { exact: true })).toHaveValue("deposit-account");
  expect(screen.getByLabelText("Zahlungstyp *", { exact: true })).toHaveValue("KAUTION");
  expect(screen.getByLabelText("Richtung *", { exact: true })).toHaveValue(direction);
  expect(screen.getByLabelText("Mieter", { exact: true })).toHaveValue("tenant");
  expect(screen.getByLabelText("Mietvertrag", { exact: true })).toHaveValue("lease");
  expect(screen.getByLabelText("Betrag *", { exact: true })).toBeRequired();
  expect(screen.getByLabelText("Wertstellungsdatum *", { exact: true })).toBeRequired();
});


describe("T40 filtered list CSV", () => {
  const route = "/api/payments";
  const outputName = "Listensaldo";
  const row = (id: string, amount: string) => ({ id, amount, direction: id === "out" ? "OUT" : "IN", bank_account_id: "account", value_date: "2026-01-01", payment_type: "KALTMIETE", status: "draft" });
  const response = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
  function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  }
  let rows: unknown[];
  let fetchMock: Mock<(input: string, options?: RequestInit) => Promise<Response>>;
  let click: MockInstance<() => void>;
  let createUrl: Mock<(blob: Blob | MediaSource) => string>;
  let revokeUrl: Mock<(url: string) => void>;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  beforeEach(() => {
    rows = [row("in", "700.00"), row("out", "50.00")];
    fetchMock = vi.fn(async (input: string, options?: RequestInit) => {
      if (options?.method && options.method !== "GET") throw new Error("No writes expected");
      if (input.startsWith(route + "/export.csv")) return new Response("\ufeffID;Betrag\r\n");
      if (input.split("?")[0] === route) return response(rows);
      if (input === "/api/bank-accounts") return response([{ id: "account", label: "Konto", property_id: "property" }]);
      if (input === "/api/properties") return response([{ id: "property", name: "Haus" }]);
      return response([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    createUrl = vi.fn().mockReturnValue("blob:test-csv");
    revokeUrl = vi.fn();
    URL.createObjectURL = createUrl;
    URL.revokeObjectURL = revokeUrl;
    click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });
  afterEach(() => {
    cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
    URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke;
  });
  function open(query = "") { return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "reader", organization_id: "org", role: "READ_ONLY" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/zahlungen" + query]}><PaymentsPage /></MemoryRouter></AuthCtx.Provider>); }
  const csv = () => screen.getByRole("button", { name: "CSV herunterladen" });
  const exportCalls = () => fetchMock.mock.calls.filter(([url]) => url.startsWith(route + "/export.csv"));
  async function loaded() { await screen.findByRole("status", { name: outputName }); }

  it.each(["userEvent", "fireEvent"])("keeps the loaded list and CSV usable when reselecting the same status with %s", async method => {
    function CurrentUrl() {
      const location = useLocation();
      return <span data-testid="current-url">{location.search}{location.hash}</span>;
    }
    rows = [
      { ...row("in", "700.00"), status: "booked" },
      { ...row("out", "50.00"), status: "booked" },
    ];
    const query = "?status=booked&bank_account_id=account&payment_type=KALTMIETE&keep=a%2Fb#selection";
    render(<MemoryRouter initialEntries={["/zahlungen" + query]}><PaymentsPage /><CurrentUrl /></MemoryRouter>);
    await loaded();
    const table = screen.getByRole("table");
    const tableBefore = table.innerHTML;
    const status = screen.getByRole("combobox", { name: "Statusfilter" });
    expect(status).toHaveValue("booked");
    if (method === "userEvent") await userEvent.setup().selectOptions(status, "booked");
    else fireEvent.change(status, { target: { value: "booked" } });
    await loaded();
    expect(screen.queryByText("Lade...")).not.toBeInTheDocument();
    expect(screen.getByRole("table")).toBe(table);
    expect(table.innerHTML).toBe(tableBefore);
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("650,00 EUR");
    expect(screen.getByTestId("current-url").textContent).toBe(query);
    expect(csv()).toBeEnabled();
    expect(fetchMock.mock.calls.filter(([url]) => url.split("?")[0] === route)).toHaveLength(1);
    fireEvent.click(csv());
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const listUrl = fetchMock.mock.calls.find(([url]) => url.split("?")[0] === route)![0];
    expect(exportCalls()[0][0]).toBe(route + "/export.csv" + listUrl.slice(route.length));
  });

  it("names each filter exactly and displays the signed sum of all displayed statuses, not a bank balance", async () => {
    open();
    for (const label of ["Statusfilter", "Bankkontofilter", "Zahlungstypfilter"]) expect(screen.getByRole("combobox", { name: label })).toBeInTheDocument();
    expect(csv()).toBeDisabled();
    await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("650,00 EUR");
    expect(screen.getByText(/angezeigten.*Zeilen/)).toBeInTheDocument();
    expect(exportCalls()).toHaveLength(0);
    expect(csv()).toBeEnabled();
  });

  it("downloads the backend Blob with precisely the visible list filters, a safe filename, and revocation", async () => {
    open("?status=booked&bank_account_id=account&payment_type=KALTMIETE&ignored=private&lease_agreement_id=hidden"); await loaded();
    fireEvent.click(csv());
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const listUrl = fetchMock.mock.calls.find(([url]) => url.split("?")[0] === route)![0];
    expect(exportCalls()[0][0]).toBe(route + "/export.csv" + listUrl.slice(route.length));
    expect(exportCalls()[0][1]!.credentials).toBe("include");
    expect(Array.from(new Uint8Array(await (createUrl.mock.calls[0][0] as Blob).arrayBuffer()))).toEqual(Array.from(new TextEncoder().encode("\ufeffID;Betrag\r\n")));
    const anchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("zahlungen.csv");
    expect(anchor.href).toBe("blob:test-csv");
    expect(anchor.isConnected).toBe(false);
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-csv");
  });

  it("uses exact large signed cents beyond the individual amount limit", async () => {
    rows = [row("one", "999999999999.99"), row("two", "999999999999.99"), row("out", "0.01")];
    open(); await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("1.999.999.999.999,97 EUR");
  });

  it("aggregates cents above Number.MAX_SAFE_INTEGER without losing a cent", async () => {
    rows = [...Array.from({ length: 100 }, (_, index) => row(`large-${index}`, "999999999999.99")), row("out", "0.01")];
    open(); await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("99.999.999.999.998,99 EUR");
  });

  it("keeps negative corrections exact", async () => {
    rows = [row("one", "-0.03"), row("two", "0.01")]; open(); await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("-0,02 EUR");
  });

  it("allows successful empty datasets to download their CSV header", async () => {
    rows = []; open(); await loaded(); fireEvent.click(csv());
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("0,00 EUR");
  });

  it.each([403, 404, 409, 422, "network"] as const)("keeps the list readable after export rejection %s and allows retry, including read-only callers", async status => {
    open(); await loaded();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
      if (url.includes("export.csv")) {
        if (status === "network") throw new Error("Netzwerkfehler");
        return new Response(JSON.stringify({ detail: "CSV nicht erlaubt" }), { status });
      }
      return defaultFetch(url, options);
    });
    fireEvent.click(csv());
    await screen.findByRole("alert");
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("650,00 EUR");
    expect(click).not.toHaveBeenCalled(); expect(createUrl).not.toHaveBeenCalled();
    expect(csv()).toBeEnabled();
    fetchMock.mockImplementation(defaultFetch);
    fireEvent.click(csv()); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  });

  it("does not export after a failed list or lookup and offers list retry", async () => {
    fetchMock.mockRejectedValue(new Error("Liste fehlgeschlagen")); open();
    await screen.findByText(/Liste fehlgeschlagen/); expect(csv()).toBeDisabled();
    expect(screen.queryByRole("status", { name: outputName })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Liste erneut laden" })).toBeEnabled();
    expect(exportCalls()).toHaveLength(0);
  });

  it("blocks export until lookup Promise.all has completed", async () => {
    const pending = deferred<Response>();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => url === "/api/properties" ? pending.promise : defaultFetch(url, options));
    open(); await act(async () => {}); expect(csv()).toBeDisabled();
    await act(async () => pending.resolve(response([{ id: "property", name: "Haus" }])));
    await loaded(); expect(csv()).toBeEnabled();
  });

  it.each(["resolve", "reject"])("ignores stale list %s, including sums and export query", async outcome => {
    const old = deferred<Response>();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => url === route ? old.promise : defaultFetch(url, options));
    open();
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
    await loaded();
    await act(async () => { if (outcome === "resolve") old.resolve(response([row("old", "999.00")])); else old.reject(new Error("Veralteter Fehler")); });
    expect(screen.queryByText(/Veralteter Fehler/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("650,00 EUR");
    fireEvent.click(csv()); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(exportCalls()[0][0]).toContain("status=booked");
  });

  it.each(["resolve", "reject"])("invalidates a pending export on filter change, late %s is inert", async outcome => {
    const pending = deferred<Response>(); open(); await loaded();
    fetchMock.mockImplementationOnce(() => pending.promise);
    fireEvent.click(csv()); fireEvent.click(csv()); expect(exportCalls()).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
    await loaded();
    await act(async () => { if (outcome === "resolve") pending.resolve(new Response("old")); else pending.reject(new Error("Veralteter Export")); });
    expect(click).not.toHaveBeenCalled(); expect(createUrl).not.toHaveBeenCalled();
    expect(screen.queryByText(/Veralteter Export/)).not.toBeInTheDocument();
    expect(csv()).toBeEnabled();
  });

  it("ignores the old export rejection while a newer export is pending", async () => {
    const old = deferred<Response>(); const latest = deferred<Response>();
    open(); await loaded();
    fetchMock.mockImplementationOnce(() => old.promise); fireEvent.click(csv());
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
    await loaded();
    fetchMock.mockImplementationOnce(() => latest.promise); fireEvent.click(csv());
    await act(async () => old.reject(new Error("Alter Exportfehler")));
    expect(csv()).toBeDisabled(); expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => latest.resolve(new Response("current")));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(csv()).toBeEnabled();
  });

  it("does not show or export the previous success when the next list fails, and can retry", async () => {
    open(); await loaded();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementationOnce(async () => { throw new Error("Aktuelle Liste fehlgeschlagen"); });
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
    await screen.findByText(/Aktuelle Liste fehlgeschlagen/);
    expect(csv()).toBeDisabled(); expect(screen.queryByRole("status", { name: outputName })).not.toBeInTheDocument();
    fetchMock.mockImplementation(defaultFetch);
    fireEvent.click(screen.getByRole("button", { name: "Liste erneut laden" }));
    await loaded(); expect(csv()).toBeEnabled();
  });

  it.each(["resolve", "reject"])("ignores export %s after unmount", async outcome => {
    const pending = deferred<Response>(); const view = open(); await loaded();
    fetchMock.mockImplementationOnce(() => pending.promise); fireEvent.click(csv()); view.unmount();
    await act(async () => { if (outcome === "resolve") pending.resolve(new Response("old")); else pending.reject(new Error("late")); });
    expect(click).not.toHaveBeenCalled(); expect(createUrl).not.toHaveBeenCalled();
  });

  it.each([["Statusfilter", "booked"], ["Bankkontofilter", "account"], ["Zahlungstypfilter", "KAUTION"]])("blocks a current list and pending download synchronously when %s changes", async (label, value) => {
    const pendingExport = deferred<Response>(); const pendingList = deferred<Response>();
    open(); await loaded();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url.includes("export.csv")) return pendingExport.promise;
      if (url.startsWith(route)) return pendingList.promise;
      return defaultFetch(url, options);
    });
    fireEvent.click(csv());
    fireEvent.change(screen.getByRole("combobox", { name: label }), { target: { value } });
    expect(csv()).toBeDisabled(); expect(screen.queryByRole("status", { name: outputName })).not.toBeInTheDocument();
    await act(async () => pendingExport.resolve(new Response("old")));
    expect(click).not.toHaveBeenCalled(); expect(csv()).toBeDisabled();
    await act(async () => pendingList.resolve(response([row("new", "12.34")])));
    await loaded(); expect(csv()).toBeEnabled();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("12,34 EUR");
  });

  it.each(["resolve", "reject"])("does not allow a stale lookup %s to overwrite current rows or error", async outcome => {
    const oldLookup = deferred<Response>();
    const defaultFetch = fetchMock.getMockImplementation()!;
    let calls = 0;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === "/api/properties" && calls++ === 0) return oldLookup.promise;
      return defaultFetch(url, options);
    });
    open();
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
    await loaded();
    await act(async () => { if (outcome === "resolve") oldLookup.resolve(response([{ id: "old", name: "Veraltetes Haus" }])); else oldLookup.reject(new Error("Alter Lookupfehler")); });
    expect(screen.queryByText(/Veraltetes Haus|Alter Lookupfehler/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("650,00 EUR");
    expect(csv()).toBeEnabled();
  });

  it.each(["resolve", "reject"])("does not update a list after unmount (%s)", async outcome => {
    const pending = deferred<Response>();
    const defaultFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => url === route ? pending.promise : defaultFetch(url, options));
    const view = open(); view.unmount();
    await act(async () => { if (outcome === "resolve") pending.resolve(response(rows)); else pending.reject(new Error("late list")); });
    expect(exportCalls()).toHaveLength(0); expect(click).not.toHaveBeenCalled();
  });

  it("guards an old export click in the same React batch as a filter change", async () => {
    open(); await loaded();
    const button = csv();
    act(() => {
      fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "booked" } });
      fireEvent.click(button);
    });
    await loaded(); expect(exportCalls()).toHaveLength(0);
  });

  it("revokes and removes the anchor even if clicking fails, then permits retry", async () => {
    open(); await loaded(); click.mockImplementationOnce(() => { throw new Error("Download fehlgeschlagen"); });
    fireEvent.click(csv()); await screen.findByRole("alert");
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-csv");
    expect(document.querySelector("a[download]")).toBeNull(); expect(csv()).toBeEnabled();
  });
});
