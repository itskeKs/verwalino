import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { AppShell } from "./AppShell";

vi.mock("./OrganizationDeletionBanner", () => ({ OrganizationDeletionBanner: () => null }));

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
});

function shell(desktop = false) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: desktop, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "OWNER_ADMIN" }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter><AppShell><button>Page action</button></AppShell></MemoryRouter>
  </AuthCtx.Provider>);
}

it("keeps closed mobile navigation out of the accessibility tree and tab order", async () => {
  shell();
  expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  await userEvent.tab();
  expect(screen.getByRole("button", { name: "Navigation umschalten" })).toHaveFocus();
});

it("opens a named mobile dialog and restores toggle focus after Escape and route selection", async () => {
  shell();
  const user = userEvent.setup();
  const toggle = screen.getByRole("button", { name: "Navigation umschalten" });
  await user.tab();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("dialog", { name: "Navigation" })).toBeInTheDocument();
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("button", { name: "Navigation schließen" })).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(toggle).toHaveFocus();
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await user.keyboard("{Enter}");
  await user.click(screen.getByRole("link", { name: "Objekte" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(toggle).toHaveFocus();
});

it("keeps desktop navigation outside a modal dialog", () => {
  shell(true);
  expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
