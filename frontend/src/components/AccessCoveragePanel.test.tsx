import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AccessCoveragePanel } from "./AccessCoveragePanel";

vi.mock("./AccessGrantProposalPanel",()=>({AccessGrantProposalPanel:()=>null}));

const snapshot = { id:"snapshot-1",generated_at:"2026-10-02T15:00:00Z",manifest_digest:"a".repeat(64),item_count:1,finding_count:1,review:null };
const item = { id:"property:property-1",kind:"property",resource_id:"property-1",references:{},findings:["DOMAIN_REQUIRED"],audience:[] };
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function setup() {
  vi.spyOn(api,"get").mockImplementation(async url=> url.includes("/items") ? {items:[item],next_cursor:null} : {items:[snapshot],next_cursor:null});
  render(<MemoryRouter><AccessCoveragePanel /></MemoryRouter>);
}

it("preserves the capture key after a lost response and shows concrete findings",async()=>{
  const post = vi.spyOn(api,"post").mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce(snapshot);
  setup();
  fireEvent.click(screen.getByRole("button",{name:"Neuen Prüfstand erfassen"}));
  await screen.findByText(/Prüfstand nicht bestätigt/);
  fireEvent.click(screen.getByRole("button",{name:"Neuen Prüfstand erfassen"}));
  await screen.findByText("Ein ausdrücklicher Datenbereich fehlt.");
  expect(post.mock.calls[0][2]).toEqual(post.mock.calls[1][2]);
  expect(screen.getByRole("link",{name:"Datenbereich und Teamfreigaben prüfen"})).toHaveAttribute("href","/gebaeudedaten-rechte");
  expect(screen.queryByRole("button",{name:/aktivieren/i})).not.toBeInTheDocument();
});

it("requires acknowledgment and retries review with its original key",async()=>{
  const post = vi.spyOn(api,"post").mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce({id:"review-1",reviewed_at:"2026-10-02T15:01:00Z",finding_count:1});
  setup();
  fireEvent.change(await screen.findByLabelText("Gespeicherten Stand auswählen"),{target:{value:snapshot.id}});
  await screen.findByText("Ein ausdrücklicher Datenbereich fehlt.");
  const button = screen.getByRole("button",{name:"Stand als geprüft vermerken"});
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));fireEvent.click(button);
  await screen.findByText(/Prüfvermerk nicht bestätigt/);
  fireEvent.click(button);
  await waitFor(()=>expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[0][2]).toEqual(post.mock.calls[1][2]);
});

it("reports stale evidence explicitly and supports failed history recovery",async()=>{
  vi.spyOn(api,"post").mockRejectedValue(new ApiError(409,"stale"));
  const get = vi.spyOn(api,"get").mockRejectedValueOnce(new Error("offline")).mockResolvedValue({items:[snapshot],next_cursor:null});
  render(<MemoryRouter><AccessCoveragePanel /></MemoryRouter>);
  await screen.findByText(/Gespeicherte Prüfstände nicht verfügbar/);
  fireEvent.click(screen.getByRole("button",{name:"Gespeicherte Prüfstände laden"}));
  await screen.findByLabelText("Gespeicherten Stand auswählen");
  get.mockResolvedValue({items:[item],next_cursor:null});
  fireEvent.change(screen.getByLabelText("Gespeicherten Stand auswählen"),{target:{value:snapshot.id}});
  await screen.findByText("Ein ausdrücklicher Datenbereich fehlt.");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button",{name:"Stand als geprüft vermerken"}));
  await screen.findByText(/Der Prüfstand ist veraltet/);
});

it("distinguishes projected combined rights from existing grants and keeps activation unavailable",async()=>{
  vi.spyOn(api,"get").mockImplementation(async url=>url.includes("/items") ? {items:[{
    ...item,kind:"bank_account",findings:["COMBINED_READER_REQUIRED"],
    references:{strict_audience:[]},audience:[{grant_id:"grant-1",user_id:"team-1",effective_capabilities:["read"],capabilities:["read"]}],
  }],next_cursor:null} : {items:[snapshot],next_cursor:null});
  render(<MemoryRouter><AccessCoveragePanel /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Gespeicherten Stand auswählen"),{target:{value:snapshot.id}});
  await screen.findByText("Keine gemeinsam ausreichenden Berechtigungen.");
  expect(screen.getByText("Teamkennung team-1: Lesen")).toBeInTheDocument();
  expect(screen.getByText(/Kein Teammitglied kann diesen Eintrag/)).toBeInTheDocument();
  expect(screen.queryByRole("button",{name:/aktivieren/i})).not.toBeInTheDocument();
});

it("renders the saved combined audience in readable language",async()=>{
  vi.spyOn(api,"get").mockImplementation(async url=>url.includes("/items") ? {items:[{
    ...item,findings:[],references:{strict_audience:[{user_id:"team-2",capabilities:["prepare","read"]}]},
  }],next_cursor:null} : {items:[snapshot],next_cursor:null});
  render(<MemoryRouter><AccessCoveragePanel /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Gespeicherten Stand auswählen"),{target:{value:snapshot.id}});
  expect(await screen.findByText("Teamkennung team-2: Vorbereiten, Lesen")).toBeInTheDocument();
  expect(screen.getByText(/Diese Vorschau erteilt keine Freigaben/)).toBeInTheDocument();
});
