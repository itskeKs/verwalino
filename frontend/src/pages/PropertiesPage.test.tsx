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
