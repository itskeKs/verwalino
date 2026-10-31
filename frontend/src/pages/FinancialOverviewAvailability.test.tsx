import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "./DashboardPage";
import { FristenmonitorPage } from "./FristenmonitorPage";
import { getMyOrganization, getFristenContext, listTasks, listFristen, listPersons, listProperties } from "../api/endpoints";
import type { DeadlineItem } from "../api/types";

vi.mock("../api/endpoints", () => ({ getOrganizationQuota: vi.fn(async () => ({ plan: "unlimited" })), getMyOrganization: vi.fn(), getFristenContext: vi.fn(), listTasks: vi.fn(), listFristen: vi.fn(), listPersons: vi.fn(), listProperties: vi.fn() }));
vi.mock("../components/AccountingScopeSelector", () => ({ AccountingScopeSelector: () => null }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: null }) }));
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getFristenContext).mockResolvedValue({ requires_accounting_scope: false });
  vi.mocked(listTasks).mockResolvedValue({ items: [], next_offset: null });
  vi.mocked(getMyOrganization).mockResolvedValue({ name: "Testverwaltung" } as Awaited<ReturnType<typeof getMyOrganization>>);
  vi.mocked(listProperties).mockResolvedValue([{}, {}] as Awaited<ReturnType<typeof listProperties>>);
  vi.mocked(listPersons).mockResolvedValue([{}] as Awaited<ReturnType<typeof listPersons>>);
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const item: DeadlineItem = { property_id: "property", kind: "BK_ABRECHNUNG", status: "OVERDUE", reference: "Altes Objekt", due_date: "2025-12-31", detail: "Abrechnung fehlt", is_statutory: true, is_provisional: false };
const expectNoAllClear = () => {
  expect(screen.queryAllByText(/alles im grünen Bereich|Alles erledigt|keine offenen Posten|Keine überfälligen oder fälligen|Alle fälligen Fristen sind erledigt|Keine Fristen ermittelt/i)).toHaveLength(0);
};

it("keeps successful master counts and marks failed dashboard finance unavailable, including while loading", async () => {
  const request = deferred<DeadlineItem[]>();
  vi.mocked(listFristen).mockReturnValue(request.promise);
  render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  expectNoAllClear();
  await act(async () => request.reject(new Error("Financial overview requires scope")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Financial overview requires scope");
  expectNoAllClear();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Testverwaltung");
  expect(within(screen.getByRole("link", { name: /^Objekte/ })).getByText("2")).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: /^Personen 1/ })).getByText("1")).toBeInTheDocument();
  for (const label of [/^Fristen überfällig/, /^Überfällige Posten/]) {
    expect(within(screen.getByRole("link", { name: label })).getByText("—")).toBeInTheDocument();
  }
});

it("does not invent missing financial counts or failed master counts", async () => {
  vi.mocked(listFristen).mockResolvedValue([]);
  vi.mocked(listProperties).mockRejectedValue(new Error("Properties unavailable"));
  render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  await screen.findByRole("alert");
  expect(within(screen.getByRole("link", { name: /^Objekte/ })).getByText("—")).toBeInTheDocument();
  expect(within(screen.getByRole("link", { name: /^Überfällige Posten/ })).getByText("—")).toBeInTheDocument();
  expect(screen.queryByText("keine offenen Posten")).toBeNull();
  expect(screen.getByText(/Keine überfälligen oder fälligen Fristen. Alles erledigt/)).toBeInTheDocument();
});

it("shows no clear or empty result when the deadline monitor fails", async () => {
  const request = deferred<DeadlineItem[]>();
  vi.mocked(listFristen).mockReturnValue(request.promise);
  render(<FristenmonitorPage />);
  expectNoAllClear();
  await act(async () => request.reject(new Error("Scope required")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Scope required");
  expectNoAllClear();
});

it("hides the previous date's deadlines while loading and after failure", async () => {
  const next = deferred<DeadlineItem[]>();
  vi.mocked(listFristen).mockResolvedValueOnce([item]).mockReturnValueOnce(next.promise);
  render(<FristenmonitorPage />);
  await screen.findByText("Altes Objekt");
  fireEvent.change(screen.getByLabelText("Stichtag"), { target: { value: "2026-01-01" } });
  expect(screen.queryByText("Altes Objekt")).toBeNull();
  expect(screen.queryByText(/1 Frist\(en\)/)).toBeNull();
  await act(async () => next.reject(new Error("Scope expired")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Scope expired");
  expect(screen.queryByText("Altes Objekt")).toBeNull();
  expectNoAllClear();
});

it("ignores an obsolete response when reference dates change quickly", async () => {
  const old = deferred<DeadlineItem[]>();
  vi.mocked(listFristen).mockReturnValueOnce(old.promise).mockRejectedValueOnce(new Error("Current date unavailable"));
  render(<FristenmonitorPage />);
  fireEvent.change(screen.getByLabelText("Stichtag"), { target: { value: "2026-01-01" } });
  await screen.findByRole("alert");
  await act(async () => old.resolve([item]));
  expect(screen.getByRole("alert")).toHaveTextContent("Current date unavailable");
  expect(screen.queryByText("Altes Objekt")).toBeNull();
  expectNoAllClear();
});

it("preserves verified zero counts and completed deadlines after a successful response", async () => {
  vi.mocked(listFristen).mockResolvedValue([{ ...item, kind: "OFFENE_POSTEN", status: "OK", detail: "0 überfällige Posten" }]);
  const dashboard = render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  await screen.findByText("keine offenen Posten");
  expect(within(screen.getByRole("link", { name: /^Überfällige Posten/ })).getByText("0")).toBeInTheDocument();
  dashboard.unmount();
  render(<FristenmonitorPage />);
  expect(await screen.findByText("Alle fälligen Fristen sind erledigt.")).toBeInTheDocument();
});
