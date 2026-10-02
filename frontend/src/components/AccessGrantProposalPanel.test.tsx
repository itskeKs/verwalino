import { afterEach,expect,it,vi } from "vitest";
import { cleanup,fireEvent,render,screen,waitFor } from "@testing-library/react";
import { api,ApiError } from "../api/client";
import { AccessGrantProposalPanel } from "./AccessGrantProposalPanel";

const items=[{id:"scope:s",kind:"accounting_scope",resource_id:"s",references:{},audience:[{grant_id:"old",user_id:"u",capabilities:["read","prepare"],revoked_at:null}]},
  {id:"member:u",kind:"member",resource_id:"u",references:{role:"CLERK",active:true},audience:[]}];
const change={domain_kind:"financial",audience_id:"s",user_id:"u",expected_grant_id:"old",capabilities:["read","export"],expires_at:null};
const proposal={id:"p",proposal_digest:"b".repeat(64),changes:[change],acceptance:null};
afterEach(()=>{cleanup();vi.restoreAllMocks();});
function panel(complete=true,reviewed=true){return <AccessGrantProposalPanel snapshotId="s1" digest={"a".repeat(64)} items={items} complete={complete} reviewed={reviewed} />;}

it("gates drafts on reviewed complete evidence and preserves the request after response loss",async()=>{
  vi.spyOn(api,"get").mockResolvedValue(null);
  const post=vi.spyOn(api,"post").mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce(proposal);
  const view=render(panel(false,false));
  const select=await screen.findByLabelText("Datenbereich");
  expect(select).toBeDisabled();view.rerender(panel());
  fireEvent.change(select,{target:{value:"scope:s"}});
  fireEvent.change(screen.getByLabelText("Teammitglied"),{target:{value:"u"}});
  fireEvent.click(screen.getByLabelText("Exportieren"));
  fireEvent.click(screen.getByRole("button",{name:"Änderung vormerken"}));
  fireEvent.click(screen.getByRole("button",{name:"Vorschlag zur Prüfung speichern"}));
  await screen.findByText(/Vorschlag nicht bestätigt/);
  fireEvent.click(screen.getByRole("button",{name:"Vorschlag zur Prüfung speichern"}));
  await screen.findByRole("button",{name:"Freigabevorschlag verbindlich annehmen"});
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(post.mock.calls[0][1]).toMatchObject({changes:[{expected_grant_id:"old",capabilities:["export","read"]}]});
});

it("loads a saved proposal and requires explicit acceptance with durable retry",async()=>{
  vi.spyOn(api,"get").mockResolvedValue(proposal);
  const post=vi.spyOn(api,"post").mockRejectedValueOnce(new Error("lost")).mockResolvedValueOnce({id:"a",accepted_at:"2026-10-02T12:00:00Z",results:[]});
  render(panel());
  const accept=await screen.findByRole("button",{name:"Freigabevorschlag verbindlich annehmen"});
  expect(accept).toBeDisabled();fireEvent.click(screen.getByRole("checkbox"));fireEvent.click(accept);
  await screen.findByText(/Annahme nicht bestätigt/);fireEvent.click(accept);
  await screen.findByText(/Annahme gespeichert/);
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(screen.queryByRole("button",{name:"Freigabevorschlag verbindlich annehmen"})).not.toBeInTheDocument();
});

it("recovers failed loads, reports stale proposals and reloads the durable receipt",async()=>{
  const get=vi.spyOn(api,"get").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(proposal);
  vi.spyOn(api,"post").mockRejectedValue(new ApiError(409,"stale"));
  render(panel());await screen.findByText(/Gespeicherter Vorschlag nicht verfügbar/);
  expect(screen.queryByLabelText("Datenbereich")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Gespeicherten Vorschlag laden"}));
  const accept=await screen.findByRole("button",{name:"Freigabevorschlag verbindlich annehmen"});
  fireEvent.click(screen.getByRole("checkbox"));fireEvent.click(accept);
  await screen.findByText(/Berechtigungen oder Prüfdaten haben sich geändert/);
  get.mockResolvedValue({...proposal,acceptance:{id:"a",accepted_at:"2026-10-02T12:00:00Z",results:[]}});
  fireEvent.click(screen.getByRole("button",{name:"Gespeicherten Vorschlag laden"}));
  await waitFor(()=>expect(screen.getByText(/Annahme gespeichert/)).toBeInTheDocument());
});

it("blocks acceptance while a reload is pending and cannot overwrite a new receipt",async()=>{
  let resolveReload!:(value:typeof proposal)=>void;
  const get=vi.spyOn(api,"get").mockResolvedValueOnce(proposal).mockImplementationOnce(()=>new Promise(resolve=>{resolveReload=resolve;}));
  const post=vi.spyOn(api,"post").mockResolvedValue({id:"a",accepted_at:"2026-10-02T12:00:00Z",results:[]});
  render(panel());const accept=await screen.findByRole("button",{name:"Freigabevorschlag verbindlich annehmen"});
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button",{name:"Gespeicherten Vorschlag laden"}));
  expect(accept).toBeDisabled();fireEvent.click(accept);expect(post).not.toHaveBeenCalled();
  await waitFor(()=>expect(get).toHaveBeenCalledTimes(2));resolveReload(proposal);
  await waitFor(()=>expect(accept).not.toBeDisabled());fireEvent.click(accept);
  await screen.findByText(/Annahme gespeichert/);expect(post).toHaveBeenCalledTimes(1);
});
