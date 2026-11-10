import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../api/endpoints";
import { MemoryRouter } from "react-router-dom";
import { MietkontoPage } from "./MietkontoPage";

vi.mock("../api/endpoints", () => ({
  listLeases: vi.fn().mockResolvedValue([{
    id: "lease-t12", unit_id: "unit-t12", tenant_profile_id: "tenant-t12",
    valid_from: "2025-01-01", valid_to: "2025-12-31",
    base_rent: "500.00", advance_payment: "100.00", deposit: null, notes: "",
  }]),
  listTenantProfiles: vi.fn().mockResolvedValue([]),
  listOwnerProfiles: vi.fn().mockResolvedValue([]),
  listPersons: vi.fn().mockResolvedValue([]),
  listUnits: vi.fn().mockResolvedValue([]),
  listProperties: vi.fn().mockResolvedValue([]),
  listEconomicPlans: vi.fn().mockResolvedValue([]),
  leaseStatement: vi.fn(),
  ownerStatement: vi.fn(),
}));
vi.mock("../components/AccountingScopeSelector", () => ({
  AccountingScopeSelector: ({ kind, capability, value, onChange }: { kind: string; capability: string; value: string | null; onChange: (value: string) => void }) => kind === "WEG"
    ? <select aria-label={`Abrechnungskreis ${capability}`} value={value || ""} onChange={e => onChange(e.target.value)}><option value="">Kreis wählen</option><option value="weg-scope">Sonnenhof WEG</option></select> : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(api.ownerStatement).mockReset();
});

it("classifies owner balances at the selected cutoff and displays the resolved due date", async () => {
  vi.mocked(api.listOwnerProfiles).mockResolvedValueOnce([{ id: "owner-due", person_id: "anna", notes: "" }]);
  vi.mocked(api.listPersons).mockResolvedValueOnce([{ id: "anna", first_name: "Anna", last_name: "Berg" }] as never);
  vi.mocked(api.ownerStatement).mockResolvedValueOnce({
    soll_total: "600.00", ist_total: "0.00", balance: "600.00",
    due_balance: "0.00", not_yet_due_balance: "600.00", open_items: [{
      period_year: 2026, period_month: 7, payment_type: "HAUSGELD_VORSCHUSS",
      soll: "600.00", ist: "0.00", open_amount: "600.00", due_date: "2026-07-03", is_due: false,
    }],
  });
  const user = userEvent.setup(); render(<MietkontoPage />);
  await user.selectOptions(await screen.findByRole("combobox", { name: "Ansicht" }), "owner");
  await user.selectOptions(screen.getByRole("combobox", { name: "Eigentuemer *" }), "owner-due");
  fireEvent.change(screen.getByLabelText("Fällig am *"), { target: { value: "2026-07-02" } });
  await user.click(screen.getByRole("button", { name: "Abrufen" }));
  const saldo = (await screen.findByText("Saldo", { exact: true })).closest("tr")!;
  expect(saldo).not.toHaveTextContent(/R[uü]e?ckstand/);
  const july = screen.getByText("2026/07").closest("tr")!;
  expect(july).toHaveTextContent("noch nicht fällig");
  expect(july).toHaveTextContent("03.07.2026");
  expect(api.ownerStatement).toHaveBeenLastCalledWith("owner-due", expect.any(String), expect.any(String), undefined, undefined, "2026-07-02");
  fireEvent.change(screen.getByLabelText("Fällig am *"), { target: { value: "2026-07-04" } });
  expect(screen.queryByText("Saldo", { exact: true })).not.toBeInTheDocument();
});

it("keeps the full-year balance and labels only the selected cutoff's due part as arrears", async () => {
  vi.mocked(api.leaseStatement).mockResolvedValue({
    soll_total: "10800.00", ist_total: "8900.00", balance: "1900.00",
    dunning_notices: [], due_balance: "100.00", not_yet_due_balance: "1800.00", open_items: [
      { period_year: 2026, period_month: 4, payment_type: "KALTMIETE", soll: "750.00", ist: "650.00", open_amount: "100.00", is_due: true },
      { period_year: 2026, period_month: 11, payment_type: "KALTMIETE", soll: "750.00", ist: "0.00", open_amount: "750.00", is_due: false },
      { period_year: 2026, period_month: 12, payment_type: "KALTMIETE", soll: "750.00", ist: "800.00", open_amount: "-50.00", is_due: false },
      { period_year: 2026, period_month: 10, payment_type: "KALTMIETE", soll: "750.00", ist: "750.00", open_amount: "0.00", is_due: true },
    ],
  });
  const user = userEvent.setup();
  render(<MemoryRouter><MietkontoPage /></MemoryRouter>);
  await user.selectOptions(await screen.findByRole("combobox", { name: "Mietvertrag *" }), "lease-t12");
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  expect(screen.getByLabelText("Fällig am *")).toHaveValue(localDate);
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: "2026-12-31" } });
  fireEvent.change(screen.getByLabelText("Fällig am *"), { target: { value: "2026-11-01" } });
  await user.click(screen.getByRole("button", { name: "Abrufen" }));
  const saldo = (await screen.findByText("Saldo", { exact: true })).closest("tr")!;
  expect(saldo).toHaveTextContent("1.900,00 €");
  expect(saldo).not.toHaveTextContent(/R[uü]e?ckstand/);
  expect(screen.getByText("davon fällig").closest("tr")).toHaveTextContent("100,00 € (Rückstand)");
  expect(screen.getAllByText("noch nicht fällig")[0].closest("tr")).toHaveTextContent("1.800,00 €");
  for (const [period, status, amount] of [["2026/04", "fällig", "100,00 €"], ["2026/11", "noch nicht fällig", "750,00 €"], ["2026/12", "noch nicht fällig", "-50,00 €"], ["2026/10", "ausgeglichen", "0,00 €"]]) {
    const row = screen.getByText(period).closest("tr")!;
    expect(within(row).getByText(status)).toBeVisible();
    expect(row).toHaveTextContent(amount);
    expect(row).not.toHaveTextContent("Rückstand");
  }
  expect(api.leaseStatement).toHaveBeenCalledWith("lease-t12", "2026-01-01", "2026-12-31", undefined, "2026-11-01");
  fireEvent.change(screen.getByLabelText("Fällig am *"), { target: { value: "2026-11-04" } });
  expect(screen.queryByText("Saldo", { exact: true })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Abrufen" }));
  expect(api.leaseStatement).toHaveBeenLastCalledWith("lease-t12", "2026-01-01", "2026-12-31", undefined, "2026-11-04");
});

describe("Mietkonto saldo (Soll minus Ist)", () => {
  it.each([
    { ist: "6600.00", balance: "600.00", shown: "600,00 €", label: null, color: "" },
    { ist: "7800.00", balance: "-600.00", shown: "-600,00 €", label: "Guthaben", color: "rgb(46, 125, 50)" },
    { ist: "7200.00", balance: "0.00", shown: "0,00 €", label: null, color: "" },
  ])("shows $balance correctly for 7200.00 Soll and $ist Ist", async ({ ist, balance, shown, label, color }) => {
    vi.mocked(api.leaseStatement).mockResolvedValue({
      soll_total: "7200.00", ist_total: ist, balance,
      dunning_notices: [], due_balance: balance, not_yet_due_balance: "0.00",
      open_items: [{
        period_year: 2025, period_month: 12, payment_type: "KALTMIETE",
        soll: "500.00", ist: "0.00", open_amount: "500.00", is_due: true,
      }, {
        period_year: 2025, period_month: 12, payment_type: "NK_VORAUSZAHLUNG",
        soll: "100.00", ist: "0.00", open_amount: "100.00", is_due: true,
      }],
    });
    const user = userEvent.setup();
    render(<MemoryRouter><MietkontoPage /></MemoryRouter>);
    const lease = await screen.findByRole("combobox", { name: /^Mietvertrag \*$/ });
    expect(lease).toHaveAccessibleName("Mietvertrag *");
    await user.selectOptions(lease, "lease-t12");
    await user.clear(screen.getByLabelText("Zeitraum von *"));
    await user.type(screen.getByLabelText("Zeitraum von *"), "2025-01-01");
    await user.clear(screen.getByLabelText("Zeitraum bis *"));
    await user.type(screen.getByLabelText("Zeitraum bis *"), "2025-12-31");
    await user.click(screen.getByRole("button", { name: "Abrufen" }));
    const saldo = (await screen.findByText("Saldo", { exact: true })).closest("tr")!;
    const amount = within(saldo).getAllByRole("cell")[1];
    expect(amount.textContent).toBe(`${shown}${label ? ` (${label})` : ""}`);
    expect(amount.style.color).toBe(color);
    const due = screen.getByText("davon fällig").closest("tr")!;
    expect(due).toHaveTextContent(`${shown}${Number(balance) > 0 ? " (Rückstand)" : Number(balance) < 0 ? " (Guthaben)" : ""}`);
    expect(api.leaseStatement).toHaveBeenCalledWith("lease-t12", "2025-01-01", "2025-12-31", undefined, expect.any(String));
  });
});


it("gives native owner account selectors exact accessible names", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><MietkontoPage /></MemoryRouter>);
  const view = await screen.findByRole("combobox", { name: "Ansicht" });
  await user.selectOptions(view, "owner");
  expect(screen.getByRole("combobox", { name: "Eigentuemer *" })).toBeVisible();
  expect(screen.getByRole("option", { name: /Sonderumlagen/ })).toBeVisible();
});


it("loads frozen levy dues for an owner without an economic plan using an explicit READ scope", async () => {
  vi.mocked(api.listOwnerProfiles).mockResolvedValueOnce([{ id: "owner-levy", person_id: "person-levy", notes: "" }]);
  vi.mocked(api.listPersons).mockResolvedValueOnce([{ id: "person-levy", first_name: "Ada", last_name: "Besitzer" }] as never);
  vi.mocked(api.ownerStatement).mockResolvedValueOnce({ soll_total: "3000.00", ist_total: "0.00", balance: "3000.00", due_balance: "3000.00", not_yet_due_balance: "0.00", open_items: [] });
  const user = userEvent.setup(); render(<MemoryRouter><MietkontoPage /></MemoryRouter>);
  await user.selectOptions(await screen.findByRole("combobox", { name: "Ansicht" }), "owner");
  await screen.findByRole("option", { name: "Ada Besitzer" });
  await user.selectOptions(screen.getByRole("combobox", { name: "Eigentuemer *" }), "owner-levy");
  await user.selectOptions(screen.getByRole("combobox", { name: "Abrechnungskreis read" }), "weg-scope");
  await user.click(screen.getByRole("button", { name: "Abrufen" }));
  await waitFor(() => expect(api.ownerStatement).toHaveBeenCalledWith("owner-levy", expect.any(String), expect.any(String), undefined, "weg-scope", expect.any(String)));
  expect(await screen.findByText("3.000,00 € (Rückstand)")).toBeVisible();
});
