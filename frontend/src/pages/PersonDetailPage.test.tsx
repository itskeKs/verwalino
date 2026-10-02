import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { PersonDetailPage } from "./PersonDetailPage";
import * as endpoints from "../api/endpoints";
import type { Person } from "../api/types";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "CLERK" } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const person: Person = { id: "one", first_name: "Private", last_name: "Contact", company_name: null,
  email: "private@example.test", phone: null, street: "", zip_code: "", city: "", iban: null, bic: null,
  bank_name: null, can_prepare: false, can_export: false };
function profiles() {
  vi.spyOn(endpoints, "listOwnerProfiles").mockResolvedValue([]);
  vi.spyOn(endpoints, "listTenantProfiles").mockResolvedValue([]);
  vi.spyOn(endpoints, "listVendors").mockResolvedValue([]);
}
function show() {
  render(<MemoryRouter initialEntries={["/personen/one"]}><Link to="/personen/two">Other contact</Link>
    <Routes><Route path="/personen/:personId" element={<PersonDetailPage />} /></Routes></MemoryRouter>);
}
it("does not offer profile writes for a readable contact without preparation rights", async () => {
  profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValue(person); show();
  await screen.findByRole("heading", { name: "Private Contact" });
  expect(screen.queryByRole("button", { name: /kennzeichnen/ })).not.toBeInTheDocument();
  expect(screen.getByText("private@example.test")).toBeVisible();
});
it("clears prior contact values when the next contact is inaccessible", async () => {
  profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValueOnce(person).mockRejectedValue(new Error("Contact not accessible"));
  show(); await screen.findByRole("heading", { name: "Private Contact" });
  fireEvent.click(screen.getByRole("link", { name: "Other contact" }));
  await screen.findByText("Error: Contact not accessible");
  expect(screen.queryByText("private@example.test")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Private Contact" })).not.toBeInTheDocument();
});
