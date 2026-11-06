import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
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
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const media = {
    matches: desktop,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => media));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "OWNER_ADMIN" }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter><AppShell><button>Page action</button></AppShell></MemoryRouter>
  </AuthCtx.Provider>);
  return (desktop: boolean) => act(() => {
    media.matches = desktop;
    listeners.forEach(listener => listener({ matches: desktop } as MediaQueryListEvent));
  });
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

it("restores navigation focus after the closed toggle becomes hidden at the desktop breakpoint", async () => {
  const resize = shell();
  await userEvent.tab();
  const toggle = screen.getByRole("button", { name: "Navigation umschalten" });
  expect(toggle).toHaveFocus();
  // Browsers blur a display:none toggle before dispatching the media change.
  toggle.blur();
  resize(true);
  expect(screen.getByRole("link", { name: "Dashboard" })).toHaveFocus();
});

it("keeps page control focus when navigation changes at the desktop breakpoint", async () => {
  const resize = shell();
  const action = screen.getByRole("button", { name: "Page action" });
  await userEvent.click(action);
  resize(true);
  expect(action).toHaveFocus();
});
