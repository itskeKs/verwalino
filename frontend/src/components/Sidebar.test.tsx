import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);
it.each(["READ_ONLY", "CLERK", "OWNER_ADMIN"])("shows the active Berichte navigation link for %s", role => {
  const onClose = vi.fn();
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/berichte"]}><Sidebar open onClose={onClose} /></MemoryRouter>
  </AuthCtx.Provider>);
  const link = screen.getByRole("link", { name: "Berichte" });
  expect(link).toHaveAttribute("href", "/berichte"); expect(link).toHaveAttribute("aria-current", "page");
  fireEvent.click(link); expect(onClose).toHaveBeenCalledTimes(1);
});
