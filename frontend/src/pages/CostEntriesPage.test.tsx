/**
 * Tests for costVocabulary.ts — validates that UI values are correctly
 * converted to DB values (and back) as required by T02.
 */

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import type { Mock, MockInstance } from "vitest";
import { AuthCtx } from "../auth/useAuth";
import { CostEntriesPage } from "./CostEntriesPage";
import {
  toDbApportionability,
  fromDbApportionability,
  toDbDistributionKey,
  fromDbDistributionKey,
  APPORTIONABILITY_LABELS,
  DISTRIBUTION_KEY_LABELS,
} from "../lib/costVocabulary";

describe("costVocabulary", () => {
  describe("apportionability labels", () => {
    it("has entries for full, partial, none", () => {
      expect(APPORTIONABILITY_LABELS.full).toBe("Voll umlagefaehig");
      expect(APPORTIONABILITY_LABELS.partial).toBe("Teilweise umlagefaehig");
      expect(APPORTIONABILITY_LABELS.none).toBe("Nicht umlagefaehig");
    });

    it("has entries for additional DB values", () => {
      expect(APPORTIONABILITY_LABELS.extern_pruefen).toBe("Umlagefaehig extern pruefen");
      expect(APPORTIONABILITY_LABELS.gemischt_pruefen).toBe("Gemischt pruefen");
      expect(APPORTIONABILITY_LABELS.manuell_pruefen).toBe("Manuell pruefen");
    });
  });

  describe("toDbApportionability", () => {
    it("converts full to UMLAGEFAEHIG", () => {
      expect(toDbApportionability("full")).toBe("UMLAGEFAEHIG");
    });

    it("converts partial to TEILWEISE_PRUEFEN", () => {
      expect(toDbApportionability("partial")).toBe("TEILWEISE_PRUEFEN");
    });

    it("converts none to NICHT_UMLAGEFAEHIG", () => {
      expect(toDbApportionability("none")).toBe("NICHT_UMLAGEFAEHIG");
    });

    it("passes through unknown DB values unchanged", () => {
      expect(toDbApportionability("GEMISCHT_PRUEFEN")).toBe("GEMISCHT_PRUEFEN");
    });
  });

  describe("fromDbApportionability", () => {
    it("converts UMLAGEFAEHIG to full", () => {
      expect(fromDbApportionability("UMLAGEFAEHIG")).toBe("full");
    });

    it("converts TEILWEISE_PRUEFEN to partial", () => {
      expect(fromDbApportionability("TEILWEISE_PRUEFEN")).toBe("partial");
    });

    it("converts NICHT_UMLAGEFAEHIG to none", () => {
      expect(fromDbApportionability("NICHT_UMLAGEFAEHIG")).toBe("none");
    });

    it("passes through unknown DB values unchanged", () => {
      expect(fromDbApportionability("IRGENDWAS")).toBe("IRGENDWAS");
    });
  });

  describe("distribution key labels", () => {
    it("has entries for all keys", () => {
      expect(DISTRIBUTION_KEY_LABELS[""]).toBe("(kein)");
      expect(DISTRIBUTION_KEY_LABELS.MEA).toBe("MEA");
      expect(DISTRIBUTION_KEY_LABELS.AREA).toBe("Flaeche");
      expect(DISTRIBUTION_KEY_LABELS.HEATED_AREA).toBe("Beheizte Flaeche");
      expect(DISTRIBUTION_KEY_LABELS.UNIT_COUNT).toBe("Einheiten");
      expect(DISTRIBUTION_KEY_LABELS.PERSON_COUNT).toBe("Personen");
      expect(DISTRIBUTION_KEY_LABELS.CONSUMPTION).toBe("Verbrauch");
      expect(DISTRIBUTION_KEY_LABELS.EXTERNAL).toBe("Extern");
      expect(DISTRIBUTION_KEY_LABELS.MANUAL).toBe("Manuell");
    });
  });

  describe("toDbDistributionKey", () => {
    it("converts AREA to WOHNFLAECHE", () => {
      expect(toDbDistributionKey("AREA")).toBe("WOHNFLAECHE");
    });

    it("converts HEATED_AREA to BEHEIZTE_FLAECHE", () => {
      expect(toDbDistributionKey("HEATED_AREA")).toBe("BEHEIZTE_FLAECHE");
    });

    it("converts UNIT_COUNT to EINHEIT", () => {
      expect(toDbDistributionKey("UNIT_COUNT")).toBe("EINHEIT");
    });

    it("converts PERSON_COUNT to PERSONENZAHL", () => {
      expect(toDbDistributionKey("PERSON_COUNT")).toBe("PERSONENZAHL");
    });

    it("converts CONSUMPTION to VERBRAUCH", () => {
      expect(toDbDistributionKey("CONSUMPTION")).toBe("VERBRAUCH");
    });

    it("converts EXTERNAL to EXTERNE_ABRECHNUNG", () => {
      expect(toDbDistributionKey("EXTERNAL")).toBe("EXTERNE_ABRECHNUNG");
    });

    it("converts MANUAL to MANUELLER_BETRAG", () => {
      expect(toDbDistributionKey("MANUAL")).toBe("MANUELLER_BETRAG");
    });

    it("passes through MEA and empty string unchanged", () => {
      expect(toDbDistributionKey("MEA")).toBe("MEA");
      expect(toDbDistributionKey("")).toBe("");
    });
  });

  describe("fromDbDistributionKey", () => {
    it("converts WOHNFLAECHE to AREA", () => {
      expect(fromDbDistributionKey("WOHNFLAECHE")).toBe("AREA");
    });

    it("converts BEHEIZTE_FLAECHE to HEATED_AREA", () => {
      expect(fromDbDistributionKey("BEHEIZTE_FLAECHE")).toBe("HEATED_AREA");
    });

    it("converts EINHEIT to UNIT_COUNT", () => {
      expect(fromDbDistributionKey("EINHEIT")).toBe("UNIT_COUNT");
    });

    it("converts PERSONENZAHL to PERSON_COUNT", () => {
      expect(fromDbDistributionKey("PERSONENZAHL")).toBe("PERSON_COUNT");
    });

    it("converts VERBRAUCH to CONSUMPTION", () => {
      expect(fromDbDistributionKey("VERBRAUCH")).toBe("CONSUMPTION");
    });

    it("converts EXTERNE_ABRECHNUNG to EXTERNAL", () => {
      expect(fromDbDistributionKey("EXTERNE_ABRECHNUNG")).toBe("EXTERNAL");
    });

    it("converts MANUELLER_BETRAG to MANUAL", () => {
      expect(fromDbDistributionKey("MANUELLER_BETRAG")).toBe("MANUAL");
    });

    it("passes through MEA and empty string unchanged", () => {
      expect(fromDbDistributionKey("MEA")).toBe("MEA");
      expect(fromDbDistributionKey("")).toBe("");
    });
  });

  describe("round-trip conversion", () => {
    it("UI value → DB → UI returns original for all apportionability values", () => {
      for (const ui of ["full", "partial", "none"]) {
        expect(fromDbApportionability(toDbApportionability(ui))).toBe(ui);
      }
    });

    it("UI value → DB → UI returns original for all distribution keys", () => {
      for (const ui of ["", "MEA", "AREA", "HEATED_AREA", "UNIT_COUNT", "PERSON_COUNT", "CONSUMPTION", "EXTERNAL", "MANUAL"]) {
        expect(fromDbDistributionKey(toDbDistributionKey(ui))).toBe(ui);
      }
    });
  });
});


describe("T40 filtered list CSV", () => {
  const route = "/api/cost-entries";
  const outputName = "Listensumme Brutto";
  const row = (id: string, amount: string) => ({ id, gross_amount: amount, property_id: "property", cost_category_id: "category", invoice_date: "2026-01-01", status: "needs_review", source: "MANUAL" });
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
    rows = [row("in", "700.00"), row("out", "-575.00")];
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
  function open(query = "") { return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "reader", organization_id: "org", role: "READ_ONLY" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/kosten" + query]}><CostEntriesPage /></MemoryRouter></AuthCtx.Provider>); }
  const csv = () => screen.getByRole("button", { name: "CSV herunterladen" });
  const exportCalls = () => fetchMock.mock.calls.filter(([url]) => url.startsWith(route + "/export.csv"));
  async function loaded() { await screen.findByRole("status", { name: outputName }); }

  it.each(["userEvent", "fireEvent"])("keeps the loaded list and CSV usable when reselecting the same status with %s", async method => {
    function CurrentUrl() {
      const location = useLocation();
      return <span data-testid="current-url">{location.search}{location.hash}</span>;
    }
    rows = [
      { ...row("in", "700.00"), status: "confirmed" },
      { ...row("out", "-575.00"), status: "confirmed" },
    ];
    const query = "?status=confirmed&property_id=property&keep=a%2Fb#selection";
    render(<MemoryRouter initialEntries={["/kosten" + query]}><CostEntriesPage /><CurrentUrl /></MemoryRouter>);
    await loaded();
    const table = screen.getByRole("table");
    const tableBefore = table.innerHTML;
    const status = screen.getByRole("combobox", { name: "Statusfilter" });
    expect(status).toHaveValue("confirmed");
    if (method === "userEvent") await userEvent.setup().selectOptions(status, "confirmed");
    else fireEvent.change(status, { target: { value: "confirmed" } });
    await loaded();
    expect(screen.queryByText("Lade...")).not.toBeInTheDocument();
    expect(screen.getByRole("table")).toBe(table);
    expect(table.innerHTML).toBe(tableBefore);
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("125,00 EUR");
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
    for (const label of ["Statusfilter", "Objektfilter"]) expect(screen.getByRole("combobox", { name: label })).toBeInTheDocument();
    expect(csv()).toBeDisabled();
    await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("125,00 EUR");
    expect(screen.getByText(/angezeigten.*Zeilen/)).toBeInTheDocument();
    expect(exportCalls()).toHaveLength(0);
    expect(csv()).toBeEnabled();
  });

  it("downloads the backend Blob with precisely the visible list filters, a safe filename, and revocation", async () => {
    open("?status=confirmed&property_id=property&ignored=private&lease_agreement_id=hidden"); await loaded();
    fireEvent.click(csv());
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    const listUrl = fetchMock.mock.calls.find(([url]) => url.split("?")[0] === route)![0];
    expect(exportCalls()[0][0]).toBe(route + "/export.csv" + listUrl.slice(route.length));
    expect(exportCalls()[0][1]!.credentials).toBe("include");
    expect(createUrl.mock.calls[0][0]).toBeInstanceOf(Blob);
    const anchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("kosten.csv");
    expect(anchor.href).toBe("blob:test-csv");
    expect(anchor.isConnected).toBe(false);
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-csv");
  });

  it("uses exact large signed cents beyond the individual amount limit", async () => {
    rows = [row("one", "999999999999.99"), row("two", "999999999999.99"), row("out", "0.01")];
    open(); await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("1.999.999.999.999,99 EUR");
  });

  it("aggregates cents above Number.MAX_SAFE_INTEGER without losing a cent", async () => {
    rows = [...Array.from({ length: 100 }, (_, index) => row(`large-${index}`, "999999999999.99")), row("out", "0.01")];
    open(); await loaded();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("99.999.999.999.999,01 EUR");
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
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("125,00 EUR");
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
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
    await loaded();
    await act(async () => { if (outcome === "resolve") old.resolve(response([row("old", "999.00")])); else old.reject(new Error("Veralteter Fehler")); });
    expect(screen.queryByText(/Veralteter Fehler/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("125,00 EUR");
    fireEvent.click(csv()); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(exportCalls()[0][0]).toContain("status=confirmed");
  });

  it.each(["resolve", "reject"])("invalidates a pending export on filter change, late %s is inert", async outcome => {
    const pending = deferred<Response>(); open(); await loaded();
    fetchMock.mockImplementationOnce(() => pending.promise);
    fireEvent.click(csv()); fireEvent.click(csv()); expect(exportCalls()).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
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
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
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
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
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

  it.each([["Statusfilter", "confirmed"], ["Objektfilter", "property"]])("blocks a current list and pending download synchronously when %s changes", async (label, value) => {
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
    fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
    await loaded();
    await act(async () => { if (outcome === "resolve") oldLookup.resolve(response([{ id: "old", name: "Veraltetes Haus" }])); else oldLookup.reject(new Error("Alter Lookupfehler")); });
    expect(screen.queryByText(/Veraltetes Haus|Alter Lookupfehler/)).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: outputName })).toHaveTextContent("125,00 EUR");
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
      fireEvent.change(screen.getByRole("combobox", { name: "Statusfilter" }), { target: { value: "confirmed" } });
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
