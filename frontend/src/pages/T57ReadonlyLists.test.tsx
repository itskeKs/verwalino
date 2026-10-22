import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { PersonsPage } from "./PersonsPage";
import { AuthCtx } from "../auth/useAuth";

vi.mock("../hooks/usePagedList", () => ({ usePagedList: () => ({
  items: [{ id: "person", first_name: "Eva", last_name: "Beispiel", can_prepare: true }, { id: "restricted", first_name: "Max", last_name: "Beispiel", can_prepare: false }],
  loading: false, error: null, nextCursor: null, reload: vi.fn(), setQuery: vi.fn(), loadMore: vi.fn(),
}) }));
afterEach(cleanup);
function show(role: string) {
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "actor", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><PersonsPage /></MemoryRouter></AuthCtx.Provider>);
}
it.each(["READ_ONLY", "UNKNOWN"])("hides person writes for %s, even with a prepare grant", role => {
  show(role);
  expect(screen.getByRole("link", { name: "Eva Beispiel" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "+ Neue Person" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Bearbeiten" })).not.toBeInTheDocument();
});
it.each(["OWNER_ADMIN", "CLERK"])("preserves %s writes and the additional prepare grant", role => {
  show(role);
  expect(screen.getByRole("button", { name: "+ Neue Person" })).toBeEnabled();
  const buttons = screen.getAllByRole("button", { name: "Bearbeiten" });
  expect(buttons[0]).toBeEnabled(); expect(buttons[1]).toBeDisabled();
});
