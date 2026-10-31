import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { Sidebar } from "./Sidebar";

afterEach(cleanup);
it("groups permissions under one active admin entry", () => {
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "OWNER_ADMIN" }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/berechtigungen/bestand"]}><Sidebar open onClose={vi.fn()} /></MemoryRouter>
  </AuthCtx.Provider>);
  expect(screen.getAllByRole("link", { name: "Berechtigungen" })).toHaveLength(1);
  expect(screen.getByRole("link", { name: "Berechtigungen" })).toHaveAttribute("aria-current", "page");
  for (const name of ["Quellenfreigaben", "Freigaben vorbereiten", "Mahnquarantäne", "Mandatsquarantäne", "Kontaktdaten-Rechte", "Gebäudedaten-Rechte"]) {
    expect(screen.queryByRole("link", { name })).not.toBeInTheDocument();
  }
  expect(screen.getByRole("link", { name: "Altquellen" })).toHaveAttribute("href", "/altquellen");
  expect(screen.getByRole("link", { name: "Seitenauszüge" })).toHaveAttribute("href", "/seitenauszuege");
});
it.each(["READ_ONLY", "CLERK", "OWNER_ADMIN"])("shows the active Berichte navigation link for %s", role => {
  const onClose = vi.fn();
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/berichte"]}><Sidebar open onClose={onClose} /></MemoryRouter>
  </AuthCtx.Provider>);
  const link = screen.getByRole("link", { name: "Berichte" });
  expect(link).toHaveAttribute("href", "/berichte"); expect(link).toHaveAttribute("aria-current", "page");
  fireEvent.click(link); expect(onClose).toHaveBeenCalledTimes(1);
});

it.each(["READ_ONLY", "CLERK", "OWNER_ADMIN"])("shows active Aufgaben navigation for %s", role => {
  const onClose = vi.fn();
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/aufgaben"]}><Sidebar open onClose={onClose} /></MemoryRouter></AuthCtx.Provider>);
  const link = screen.getByRole("link", { name: "Aufgaben" });
  expect(link).toHaveAttribute("href", "/aufgaben"); expect(link).toHaveAttribute("aria-current", "page");
  fireEvent.click(link); expect(onClose).toHaveBeenCalledOnce();
});
