import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type { EconomicPlan, Property } from "../api/types";
import { WegSettlementsPage } from "./WegSettlementsPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["WEG-Objekt *", "Wirtschaftsplan (Soll) *"])("explicitly labels %s without including its options", async name => {
  const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
  const plan: EconomicPlan = { id: "plan-2026", organization_id: "org", property_id: property.id, management_mandate_id: null, plan_year: 2026, period_start: "2026-01-01", period_end: "2026-12-31", monthly_due_day: 1, reserve_contribution_total: "0.00", status: "finalized", snapshot_sha256: "a".repeat(64), engine_version: "1", finalized_at: "2026-01-01T10:00:00Z", finalized_by: "admin", superseded_by_id: null, lines: [] };
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/weg-settlements?run_type=WEG_JAHRESABRECHNUNG") return [] as never;
    if (path === "/api/economic-plans") return [plan] as never;
    if (path === "/api/properties") return [property] as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  render(<MemoryRouter><WegSettlementsPage /></MemoryRouter>);
  await screen.findByText("Keine WEG-Abrechnungen vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neue WEG-Abrechnung" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: property.id } });
  const select = screen.getByRole("combobox", { name });
  expect(select).toHaveAttribute("aria-label", name);
  expect(select).toHaveAccessibleName(name);
  if (name === "Wirtschaftsplan (Soll) *") {
    expect(select).toHaveTextContent("Wirtschaftsplan 2026 (finalisiert)");
    fireEvent.change(select, { target: { value: plan.id } });
    expect(select).toHaveValue(plan.id);
  } else {
    expect(select).toHaveTextContent("WEG Haus (WEG)");
    expect(select).toHaveValue(property.id);
  }
});
