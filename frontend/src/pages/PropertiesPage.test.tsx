import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PropertiesPage } from "./PropertiesPage";
import { api } from "../api/client";

const auth = vi.hoisted(() => ({ role: "OWNER_ADMIN", user_id: "publisher" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: auth }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); auth.role = "OWNER_ADMIN"; });

it("requires explicit audience choice and retains creation payload/key after a lost response", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost")).mockResolvedValue({ id: "created" });
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Objekt" }));
  const audience = screen.getByRole("checkbox", { name: /Datenbereich anlegen/ });
  expect(audience).not.toBeChecked();
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Reviewed house" } });
  fireEvent.click(audience);
  fireEvent.change(screen.getByLabelText("Begründung der Freigabe"), { target: { value: "Responsible for this building" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Speichern" })).not.toBeInTheDocument());
  expect(post.mock.calls).toHaveLength(2);
  expect(post.mock.calls[0][1]).toEqual(post.mock.calls[1][1]);
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
  expect(new Headers(post.mock.calls[0][2]?.headers).get("Idempotency-Key")).toBeTruthy();
  expect(post.mock.calls[0][1]).toMatchObject({ data_audience: {
    label: "Reviewed house", reason: "Responsible for this building", grants: [{
      user_id: "publisher", capabilities: ["read", "prepare", "approve", "export"], reason: "Responsible for this building",
    }],
  } });
});

it("does not offer administrative self-grants to a clerk", async () => {
  auth.role = "CLERK";
  vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Objekt" }));
  expect(screen.queryByRole("checkbox", { name: /Datenbereich anlegen/ })).not.toBeInTheDocument();
});

it("explicitly labels the management select without including its option text", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.startsWith("/api/properties/page")) return { items: [], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>);
  await screen.findByText("Keine Objekte vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Objekt" }));
  const select = screen.getByRole("combobox", { name: "Verwaltungsart *" });
  expect(select).toHaveAttribute("aria-label", "Verwaltungsart *");
  expect(select).toHaveAccessibleName("Verwaltungsart *");
  expect(select).toHaveTextContent("Mietobjekt");
  fireEvent.change(select, { target: { value: "WEG" } });
  expect(select).toHaveValue("WEG");
});

it("names the settlement start and shows the inclusive month pattern", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Objekt" }));
  const month = screen.getByRole("combobox", { name: "Beginn des Abrechnungszeitraums" });
  fireEvent.change(month, { target: { value: "4" } });
  expect(screen.getByText("01.04. bis 31.03.")).toBeVisible();
});
