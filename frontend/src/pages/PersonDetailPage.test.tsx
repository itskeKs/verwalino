import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { PersonDetailPage } from "./PersonDetailPage";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Person } from "../api/types";

vi.mock("../components/AttachmentsPanel", () => ({
  AttachmentsPanel: ({ propertyId, unitId, personId, canWrite }: { propertyId?: string; unitId?: string; personId?: string; canWrite: boolean }) =>
    <section aria-label="Dokumente" data-property={propertyId} data-unit={unitId} data-person={personId} data-can-write={String(canWrite)} />,
}));

const authRole = vi.hoisted(() => ({ value: "CLERK", get role() { return this.value; } }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: authRole }) }));
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
it("keeps contact data while refreshing a saved tenant designation", async () => {
  profiles();
  let finish!: (value: Person) => void;
  const get = vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true });
  vi.spyOn(endpoints, "createTenantProfile").mockResolvedValue({ id: "tenant" } as never);
  show();
  const panel = await screen.findByRole("region", { name: "Dokumente" });
  get.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  vi.mocked(endpoints.listTenantProfiles).mockResolvedValue([{ id: "tenant", person_id: "one" }] as never);
  fireEvent.click(screen.getByRole("button", { name: "Als Mieter kennzeichnen" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Mieterprofil gespeichert.");
  expect(screen.getByText("private@example.test")).toBeVisible();
  expect(panel).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Als Mieter kennzeichnen" })).not.toBeInTheDocument();
  expect(endpoints.createTenantProfile).toHaveBeenCalledOnce();
  await act(async () => finish({ ...person, can_prepare: true }));
  expect(await screen.findByText("Mieter", { exact: true })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Als Mieter kennzeichnen" })).not.toBeInTheDocument();
});

it("retains useful contact data after a failed refresh and reports the failure", async () => {
  profiles();
  const get = vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true });
  vi.spyOn(endpoints, "createTenantProfile").mockResolvedValue({ id: "tenant" } as never);
  show(); await screen.findByRole("heading", { name: "Private Contact" });
  get.mockRejectedValueOnce(new Error("Nachladen fehlgeschlagen"));
  fireEvent.click(screen.getByRole("button", { name: "Als Mieter kennzeichnen" }));
  await screen.findByText("Error: Nachladen fehlgeschlagen");
  expect(screen.getByText("private@example.test")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Mieterprofil gespeichert.");
  expect(screen.queryByRole("button", { name: "Als Mieter kennzeichnen" })).not.toBeInTheDocument();
});

it("does not confirm a failed tenant designation", async () => {
  profiles(); vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true });
  vi.spyOn(endpoints, "createTenantProfile").mockRejectedValue(new Error("Speichern fehlgeschlagen"));
  show(); fireEvent.click(await screen.findByRole("button", { name: "Als Mieter kennzeichnen" }));
  await screen.findByText("Error: Speichern fehlgeschlagen");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Als Mieter kennzeichnen" })).toBeEnabled();
});
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


it("clears retained contact values when a refresh confirms lost access", async () => {
  profiles();
  const get = vi.spyOn(endpoints, "getPerson").mockResolvedValue({ ...person, can_prepare: true });
  vi.spyOn(endpoints, "createTenantProfile").mockResolvedValue({ id: "tenant" } as never);
  show(); await screen.findByRole("heading", { name: "Private Contact" });
  get.mockRejectedValueOnce(new ApiError(403, "Zugriff verweigert"));
  fireEvent.click(screen.getByRole("button", { name: "Als Mieter kennzeichnen" }));
  await screen.findByText(/Zugriff verweigert/);
  expect(screen.queryByText("private@example.test")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});
