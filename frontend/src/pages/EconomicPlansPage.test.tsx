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

it("prefills property periods and refreshes them when the start year changes", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans") return [] as never;
    if (path === "/api/properties") return [{ id: "april", name: "Aprilhaus", management_type: "WEG", fiscal_year_start_month: 4 }] as never;
    if (path.includes("/settlement-periods")) {
      const year = Number(new URL(path, "http://local").searchParams.get("year"));
      return [{ year, label: `${year}/${year + 1}`, period_start: `${year}-04-01`, period_end: `${year + 1}-03-31` }] as never;
    }
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(path);
  });
  render(<MemoryRouter><EconomicPlansPage /></MemoryRouter>);
  await screen.findByText("Keine Wirtschaftspläne vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neuer Wirtschaftsplan" }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "april" } });
  fireEvent.change(screen.getByLabelText("Wirtschaftsjahr *"), { target: { value: "2025" } });
  await screen.findByRole("option", { name: "2025/2026" });
  expect(screen.getByLabelText("Zeitraum von *")).toHaveValue("2025-04-01");
  expect(screen.getByLabelText("Zeitraum bis *")).toHaveValue("2026-03-31");
});
