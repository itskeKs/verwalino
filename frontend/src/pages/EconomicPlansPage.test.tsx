import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type { Property } from "../api/types";
import { EconomicPlansPage } from "./EconomicPlansPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("explicitly labels the WEG property select without including property options", async () => {
  const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [property] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  render(<MemoryRouter><EconomicPlansPage /></MemoryRouter>);
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  const select = screen.getByRole("combobox", { name: "WEG-Objekt *" });
  expect(select).toHaveAttribute("aria-label", "WEG-Objekt *");
  expect(select).toHaveAccessibleName("WEG-Objekt *");
  expect(select).toHaveTextContent("WEG Haus (WEG)");
  fireEvent.change(select, { target: { value: property.id } });
  expect(select).toHaveValue(property.id);
});
