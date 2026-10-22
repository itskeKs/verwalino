import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MeetingsPage } from "./MeetingsPage";
import { MeetingDetailPage } from "./MeetingDetailPage";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { user_id: "user", organization_id: "org", role: "CLERK" }, loading: false }) }));
vi.mock("../api/mail", () => ({ getMailDispatch: vi.fn().mockResolvedValue({ recipients: [], attempts: [] }), sendMailDispatch: vi.fn() }));

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
const meeting = { id: "meeting-1", property_id: "property-1", meeting_date: "2026-01-15", meeting_type: "ORDENTLICH", status: "PLANNED", location: "Testsaal", invitation_text: null, protocol_text: null };
const property = { id: "property-1", name: "T13 WEG", management_type: "WEG" };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it.each(["ORDENTLICH", "AUSSERORDENTLICH"])("creates a meeting with API type %s", async (type) => {
  const create = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/properties") return json([property]);
    if (init?.method === "POST") { create(JSON.parse(init.body as string)); return json(meeting); }
    return json([]);
  }));
  render(<MemoryRouter><MeetingsPage /></MemoryRouter>);
  await screen.findByRole("option", { name: "T13 WEG" });
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Versammlung" }));
  fireEvent.change(screen.getByLabelText(/WEG-Objekt/), { target: { value: "property-1" } });
  fireEvent.change(screen.getByLabelText(/Datum/), { target: { value: "2026-01-15" } });
  if (type === "AUSSERORDENTLICH") fireEvent.change(screen.getByLabelText(/Typ/), { target: { value: type } });
  fireEvent.click(screen.getByRole("button", { name: "Versammlung anlegen" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ meeting_type: type })));
});

it("filters API meeting statuses and renders their labels", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => json(url === "/api/properties" ? [property] : [meeting, { ...meeting, id: "held-1", status: "HELD", location: "Erledigt" }])));
  render(<MemoryRouter initialEntries={["/?status=PLANNED"]}><MeetingsPage /></MemoryRouter>);
  expect(await screen.findByText("Testsaal")).toBeInTheDocument();
  expect(screen.queryByText("Erledigt")).not.toBeInTheDocument();
  expect(screen.getByText("Geplant", { selector: "span" })).toBeInTheDocument();
  expect(screen.getByText("Ordentlich", { selector: "td" })).toBeInTheDocument();
});

it.each(["ANGENOMMEN", "ABGELEHNT"])("creates a TOP resolution with API result %s and transfers after HELD", async (result) => {
  const create = vi.fn(); const transfer = vi.fn();
  let current = { ...meeting }; const resolutions: unknown[] = [];
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/properties") return json([property]);
    if (url.endsWith("/agenda")) return json([{ id: "top-1", meeting_id: meeting.id, item_number: 1, title: "Jahresabrechnung 2025" }]);
    if (url.endsWith("/hold")) { current = { ...current, status: "HELD" }; return json(current); }
    if (url.endsWith("/register") && init?.method === "POST") { transfer(); return json({ id: "entry-1" }); }
    if (url.includes("/resolutions")) {
      if (init?.method === "POST") { const body = JSON.parse(init.body as string); create(body); resolutions.push({ ...body, id: "resolution-1" }); return json(resolutions[0]); }
      return json(resolutions);
    }
    return json(current);
  }));
  render(<MemoryRouter initialEntries={["/versammlungen/meeting-1"]}><Routes><Route path="/versammlungen/:meetingId" element={<MeetingDetailPage />} /></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "+ Beschluss" }));
  fireEvent.change(screen.getByLabelText(/Beschlusswortlaut/), { target: { value: "T13 Abrechnung beschlossen" } });
  fireEvent.change(screen.getByLabelText(/Stimmen Ja/), { target: { value: "2" } });
  if (result === "ABGELEHNT") fireEvent.change(screen.getByLabelText(/Ergebnis/), { target: { value: result } });
  fireEvent.click(screen.getByRole("button", { name: "Beschluss erfassen" }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ result, agenda_item_id: "top-1", votes_yes: 2 })));
  await screen.findByText(result === "ANGENOMMEN" ? "Angenommen" : "Abgelehnt", { selector: "span" });
  fireEvent.click(screen.getByRole("button", { name: "Als durchgefuehrt markieren" }));
  fireEvent.click(await screen.findByRole("button", { name: "In Beschluss-Sammlung" }));
  await waitFor(() => expect(transfer).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: "+ TOP hinzufuegen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Beschluss" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Uebernommen" })).toBeDisabled();
});
