import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { PersonDetailPage } from "./PersonDetailPage";
import * as endpoints from "../api/endpoints";
import type { Person } from "../api/types";

vi.mock("../components/AttachmentsPanel", () => ({
  AttachmentsPanel: ({ propertyId, unitId, personId, canWrite }: { propertyId?: string; unitId?: string; personId?: string; canWrite: boolean }) =>
    <section aria-label="Dokumente" data-property={propertyId} data-unit={unitId} data-person={personId} data-can-write={String(canWrite)} />,
}));

const authRole = vi.hoisted(() => ({ value: "CLERK" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: authRole.value } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); authRole.value = "CLERK"; });
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

it("T37 adds person documents without inventing a property or replacing contact grants", async () => {
  profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValue(person); show();
  const panel = await screen.findByRole("region", { name: "Dokumente" });
  expect(panel).toHaveAttribute("data-person", "one"); expect(panel).not.toHaveAttribute("data-property");
  expect(panel).toHaveAttribute("data-can-write", "false"); expect(screen.getByText("private@example.test")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Rollen / Profile" })).toBeVisible();
});
it("T37 permits document preparation only with the existing person preparation grant", async () => {
  profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true }); show();
  expect(await screen.findByRole("region", { name: "Dokumente" })).toHaveAttribute("data-can-write", "true");
});

it.each(["READ_ONLY", "UNKNOWN"])("T37 keeps document actions read-only for person role %s even with a prepare flag", async (role) => {
  authRole.value = role; profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true }); show();
  expect(await screen.findByRole("region", { name: "Dokumente" })).toHaveAttribute("data-can-write", "false");
});
