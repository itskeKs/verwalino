import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "./DashboardPage";
import { FristenmonitorPage } from "./FristenmonitorPage";
import { getMyOrganization, getFristenContext, listTasks, listFristen, listPersons, listProperties } from "../api/endpoints";
import type { DeadlineItem } from "../api/types";

vi.mock("../api/endpoints", () => ({ getMyOrganization: vi.fn(), getFristenContext: vi.fn(), listTasks: vi.fn(), listFristen: vi.fn(), listPersons: vi.fn(), listProperties: vi.fn() }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: null }) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getFristenContext).mockResolvedValue({ requires_accounting_scope: false });
  vi.mocked(listTasks).mockResolvedValue({ items: [], next_offset: null });
  vi.mocked(getMyOrganization).mockResolvedValue({ name: "Test" } as Awaited<ReturnType<typeof getMyOrganization>>);
  vi.mocked(listProperties).mockResolvedValue([]);
  vi.mocked(listPersons).mockResolvedValue([]);
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items: [
    { id: "rental", label: "Mietkreis", scope_type: "MIET", valid_from: "2025-01-01", valid_to: null },
    { id: "weg", label: "WEG-Kreis", scope_type: "WEG", valid_from: "2025-01-01", valid_to: null },
  ], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } })));
});
const row: DeadlineItem = { property_id: "p", kind: "BK_ABRECHNUNG", status: "OVERDUE", reference: "Mietkreis-Frist", due_date: "2025-12-31", detail: "Offen", is_statutory: true, is_provisional: false };

it("changes monitor scope without retaining another scope's rows or accepting an obsolete reply", async () => {
  let finishOld!: (rows: DeadlineItem[]) => void;
  vi.mocked(listFristen).mockRejectedValueOnce(new Error("Select scope"))
    .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve; }))
    .mockResolvedValueOnce([{ ...row, reference: "WEG-Frist", kind: "WEG_JAHRESABRECHNUNG" }]);
  render(<FristenmonitorPage />);
  await screen.findByRole("alert");
  await screen.findByRole("option", { name: /Mietkreis/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "rental" } });
  expect(listFristen).toHaveBeenLastCalledWith(expect.any(String), "rental");
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "weg" } });
  await screen.findByText("WEG-Frist");
  await act(async () => finishOld([row]));
  expect(screen.queryByText("Mietkreis-Frist")).toBeNull();
  expect(screen.getByText("WEG-Frist")).toBeInTheDocument();
});

it("dashboard recovers with a scoped result and does not invent a WEG tenant-debt zero", async () => {
  vi.mocked(listFristen).mockRejectedValueOnce(new Error("Select scope"))
    .mockResolvedValueOnce([{ ...row, reference: "WEG-Frist", kind: "WEG_JAHRESABRECHNUNG" }]);
  render(<MemoryRouter><DashboardPage /></MemoryRouter>);
  await screen.findByRole("alert");
  await screen.findByRole("option", { name: /WEG-Kreis/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "weg" } });
  await screen.findByText("WEG-Frist");
  expect(listFristen).toHaveBeenLastCalledWith(expect.any(String), "weg");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(within(screen.getByRole("link", { name: /^Überfällige Posten/ })).getByText("—")).toBeInTheDocument();
  expect(screen.queryByText("keine offenen Posten")).toBeNull();
});
