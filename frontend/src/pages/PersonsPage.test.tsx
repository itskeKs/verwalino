import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { PersonsPage } from "./PersonsPage";

const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("keeps the person list mounted during a saved edit refresh", async () => {
  let finish!: (value: Response) => void;
  let saved = false;
  const person = { id: "person", first_name: "Anna", last_name: "Beispiel", street: "", zip_code: "", city: "", can_prepare: true };
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "PUT") { saved = true; return json({ ...person, last_name: "Neu" }); }
    return saved ? new Promise<Response>(resolve => { finish = resolve; }) : json({ items: [person], next_cursor: null });
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "CLERK" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><PersonsPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole("link", { name: "Anna Beispiel" });
  const table = screen.getByRole("table");
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Nachname *"), { target: { value: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Person gespeichert.");
  expect(table).toBeInTheDocument();
  await act(async () => finish(json({ items: [{ ...person, last_name: "Neu" }], next_cursor: null })));
  expect(await screen.findByRole("link", { name: "Anna Neu" })).toBeVisible();
});
