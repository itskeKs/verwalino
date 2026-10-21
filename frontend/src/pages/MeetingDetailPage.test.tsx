import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MeetingDetailPage } from "./MeetingDetailPage";
import { AuthCtx } from "../auth/useAuth";

const meeting = { id: "meeting-1", property_id: "property-1", meeting_date: "2026-11-15", meeting_type: "ORDENTLICH", status: "PLANNED", location: "Saal", invitation_text: "Willkommen", protocol_text: null };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function mount(role = "OWNER_ADMIN") {
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user-1", organization_id: "org-1", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/versammlungen/meeting-1"]}><Routes><Route path="/versammlungen/:meetingId" element={<MeetingDetailPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(["OWNER_ADMIN", "CLERK", "READ_ONLY"])("opens the actual invitation PDF URL in a new tab for %s", async (role) => {
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/properties") return json([{ id: "property-1", name: "WEG" }]);
    if (url.endsWith("/agenda") || url.endsWith("/resolutions")) return json([]);
    return json(meeting);
  }));
  mount(role);
  fireEvent.click(await screen.findByRole("button", { name: "Einladung als PDF" }));
  expect(open).toHaveBeenCalledWith("/api/meetings/meeting-1/invitation.pdf", "_blank", "noopener,noreferrer");
});

it("does not offer invitation export while loading", () => {
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  mount();
  expect(screen.queryByRole("button", { name: "Einladung als PDF" })).not.toBeInTheDocument();
});

it("does not offer invitation export after a denied meeting load", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Meeting source access unavailable" }, 404)));
  mount();
  expect(await screen.findByText(/Meeting source access unavailable/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Einladung als PDF" })).not.toBeInTheDocument();
});

it.each([true, false])("uses backend short-notice metadata (%s), not browser date arithmetic", async (shortNotice) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url === "/api/properties" || url.endsWith("/agenda") || url.endsWith("/resolutions")) return json([]);
    return json({ ...meeting, meeting_date: "2000-01-01", invitation_short_notice: shortNotice });
  }));
  mount();
  await screen.findByRole("button", { name: "Einladung als PDF" });
  if (shortNotice) expect(screen.getByText(/Weniger als drei Wochen/)).toBeInTheDocument();
  else expect(screen.queryByText(/Weniger als drei Wochen/)).not.toBeInTheDocument();
});
