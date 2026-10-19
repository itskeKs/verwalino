import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../api/endpoints";
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
  AccountingScopeSelector: () => null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Mietkonto saldo (Soll minus Ist)", () => {
  it.each([
    { ist: "6600.00", balance: "600.00", label: "Rueckstand", color: "rgb(183, 28, 28)" },
    { ist: "7800.00", balance: "-600.00", label: "Guthaben", color: "rgb(46, 125, 50)" },
    { ist: "7200.00", balance: "0.00", label: null, color: "" },
  ])("shows $balance correctly for 7200.00 Soll and $ist Ist", async ({ ist, balance, label, color }) => {
    vi.mocked(api.leaseStatement).mockResolvedValue({
      soll_total: "7200.00", ist_total: ist, balance,
      open_items: [{
        period_year: 2025, period_month: 12, payment_type: "KALTMIETE",
        soll: "500.00", ist: "0.00", open_amount: "500.00",
      }, {
        period_year: 2025, period_month: 12, payment_type: "NK_VORAUSZAHLUNG",
        soll: "100.00", ist: "0.00", open_amount: "100.00",
      }],
    });
    const user = userEvent.setup();
    render(<MietkontoPage />);
    await user.selectOptions(await screen.findByLabelText(/^Mietvertrag \*/), "lease-t12");
    await user.clear(screen.getByLabelText("Zeitraum von *"));
    await user.type(screen.getByLabelText("Zeitraum von *"), "2025-01-01");
    await user.clear(screen.getByLabelText("Zeitraum bis *"));
    await user.type(screen.getByLabelText("Zeitraum bis *"), "2025-12-31");
    await user.click(screen.getByRole("button", { name: "Abrufen" }));
    const saldo = (await screen.findByText("Saldo", { exact: true })).closest("tr")!;
    const amount = within(saldo).getAllByRole("cell")[1];
    expect(amount.textContent).toBe(`${balance} EUR${label ? ` (${label})` : ""}`);
    expect(amount.style.color).toBe(color);
    expect(api.leaseStatement).toHaveBeenCalledWith("lease-t12", "2025-01-01", "2025-12-31", undefined);
  });
});
