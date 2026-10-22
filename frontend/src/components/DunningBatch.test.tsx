import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DunningBatch } from "./DunningBatch";

const auth = vi.hoisted(() => ({ session: { role: "CLERK", organization_id: "org", user_id: "user" } }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => auth }));
vi.mock("./AccountingScopeSelector", () => ({ AccountingScopeSelector: (p: { kind: string; capability: string; onChange: (id: string) => void; onValidityChange: (valid: boolean) => void }) => <button type="button" onClick={() => { expect(p.kind).toBe("MIET"); expect(p.capability).toBe("prepare"); p.onChange("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"); p.onValidityChange(true); }}>Vorbereitungskreis wählen</button> }));
const candidates = [1, 2, 3, 4].map(n => ({ lease_agreement_id: `00000000-0000-4000-8000-00000000000${n}`, tenant_profile_id: `tenant${n}`, person_name: `Person ${n}`, unit_number: `${n}`, property_name: "Haus", principal_amount: "0.10", fee_amount: "0.02", interest_amount: "0.00", total_amount: "0.12", level: 1 }));
const props = { periodStart: "2026-01-01", periodEnd: "2026-12-31", properties: [{ id: "property", name: "Haus" }] };
const preview = { candidates, source_digest: "digest" };
beforeEach(() => {
  auth.session = { role: "CLERK", organization_id: "org", user_id: "user" };
  vi.spyOn(api, "get").mockResolvedValue({ requires_accounting_scope: false });
  vi.spyOn(api, "post").mockImplementation(async url => url.endsWith("preview") ? preview : candidates.slice(0, 3).map((c, n) => ({ ...c, id: `notice${n}`, status: "draft" })));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show() { return render(<MemoryRouter><DunningBatch {...props} /></MemoryRouter>); }
async function prepare() {
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Mindestrückstand (EUR) *"), { target: { value: "0.01" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
}
function selectThree() { for (const n of [1, 2, 3]) fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`Person ${n}`) })); }
async function submitThree() {
  selectThree();
  fireEvent.click(screen.getByRole("button", { name: "Auswahl bestätigen" }));
  const dialog = screen.getByRole("dialog", { name: "Mahnentwürfe anlegen" });
  expect(dialog).toHaveTextContent("3 Mahnentwürfe");
  expect(dialog).toHaveTextContent("0.30 EUR");
  expect(dialog).toHaveTextContent("0.36 EUR");
  fireEvent.click(within(dialog).getByRole("button", { name: "Mahnentwürfe anlegen" }));
}
it("loads nothing while closed, requires explicit input and creates only the selected three drafts", async () => {
  show(); expect(api.post).not.toHaveBeenCalled(); expect(api.get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  expect(screen.getByLabelText("Mindestrückstand (EUR) *")).toHaveValue("");
  expect(screen.getByLabelText("Stichtag *")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf schließen" }));
  await prepare();
  expect(screen.getAllByRole("checkbox").every(c => !(c as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole("button", { name: "Auswahl bestätigen" })).toBeDisabled();
  await submitThree();
  await screen.findByRole("link", { name: "Entwurf notice0 öffnen" });
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch", {
    period_start: "2026-01-01", period_end: "2026-12-31", reference_date: "2026-10-01", threshold: "0.01", grace_days: 0, base_interest_rate: null,
    lease_agreement_ids: candidates.slice(0, 3).map(c => c.lease_agreement_id), expected_source_digest: "digest", request_id: expect.any(String),
  });
  expect(screen.getByText(/nicht ausgestellt/)).toBeInTheDocument();
});
it("invalidates the entire preview on edits, scope and inherited period changes", async () => {
  const view = show(); await prepare(); selectThree();
  fireEvent.change(screen.getByLabelText("Karenztage"), { target: { value: "2" } });
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" })); await screen.findByRole("checkbox", { name: /Person 1/ });
  fireEvent.change(screen.getByLabelText("Immobilie für Abrechnungskreis"), { target: { value: "property" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorbereitungskreis wählen" }));
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" })); await screen.findByRole("checkbox", { name: /Person 1/ });
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch/preview", expect.objectContaining({ accounting_scope_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }));
  view.rerender(<MemoryRouter><DunningBatch {...props} periodEnd="2026-11-30" /></MemoryRouter>);
  expect(screen.queryByRole("checkbox")).toBeNull();
});
it("discards a late preview after input changes and exposes a retryable conflict", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  show(); fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Mindestrückstand (EUR) *"), { target: { value: "1" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  fireEvent.change(screen.getByLabelText("Mindestrückstand (EUR) *"), { target: { value: "2" } });
  await act(async () => resolve(preview)); expect(screen.queryByRole("checkbox")).toBeNull();
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError(409, "Datengrundlage geändert"));
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Datengrundlage geändert");
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
});
it("preserves the exact request UUID and body after an uncertain result, even across disclosure toggles", async () => {
  show(); await prepare();
  vi.mocked(api.post).mockRejectedValueOnce(new Error("Netzwerk unterbrochen"));
  await submitThree(); await screen.findByRole("alert");
  const body = vi.mocked(api.post).mock.calls.at(-1)![1];
  expect(screen.getByLabelText("Mindestrückstand (EUR) *")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf schließen" }));
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  fireEvent.click(screen.getByRole("button", { name: "Identischen Auftrag erneut versuchen" }));
  await screen.findByRole("link", { name: "Entwurf notice0 öffnen" });
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch", body);
});
it("offers explicit select-all and hides all controls for read-only sessions", async () => {
  const view = show(); await prepare();
  fireEvent.click(screen.getByRole("button", { name: "Alle geeigneten Rückstände auswählen" }));
  expect(screen.getAllByRole("checkbox").every(c => (c as HTMLInputElement).checked)).toBe(true);
  auth.session = { ...auth.session, role: "READ_ONLY" };
  view.rerender(<MemoryRouter><DunningBatch {...props} /></MemoryRouter>);
  expect(screen.queryByRole("button", { name: /Mahnlauf/ })).toBeNull();
});

it("requires a preparation scope when the session policy demands one and retries unknown policy explicitly", async () => {
  vi.mocked(api.get).mockRejectedValueOnce(new ApiError(403, "Policy nicht zugänglich"))
    .mockResolvedValue({ requires_accounting_scope: true });
  show(); fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Policy nicht zugänglich");
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Mindestrückstand (EUR) *"), { target: { value: "0" } });
  expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Berechtigung erneut prüfen" }));
  await screen.findByText(/Für diesen Mahnlauf einen MIET-Abrechnungskreis/);
  expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Immobilie für Abrechnungskreis"), { target: { value: "property" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorbereitungskreis wählen" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch/preview", expect.objectContaining({ threshold: "0.00", accounting_scope_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }));
});

it.each([403, 409, 422])("invalidates a rejected creation (%s) without automatically retrying", async status => {
  show(); await prepare();
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError(status, `Fehler ${status}`));
  await submitThree();
  expect(await screen.findByRole("alert")).toHaveTextContent(`Fehler ${status}`);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Identischen Auftrag erneut versuchen" })).toBeNull();
  expect(screen.getByLabelText("Mindestrückstand (EUR) *")).toBeEnabled();
  expect(api.post).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
  expect(screen.getAllByRole("checkbox").every(c => !(c as HTMLInputElement).checked)).toBe(true);
});

it("retries server failures with the same immutable body and prevents double submission", async () => {
  show(); await prepare();
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError(503, "Server ungewiss"));
  await submitThree(); await screen.findByRole("alert");
  const body = vi.mocked(api.post).mock.calls.at(-1)![1];
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  const retry = screen.getByRole("button", { name: "Identischen Auftrag erneut versuchen" });
  fireEvent.click(retry); fireEvent.click(retry);
  expect(api.post).toHaveBeenCalledTimes(3);
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch", body);
  await act(async () => resolve([{ id: "done", status: "draft" }]));
  await screen.findByRole("link", { name: "Entwurf done öffnen" });
});

it("does not resurrect a preview after changing the period away and back", async () => {
  const view = show(); await prepare(); selectThree();
  view.rerender(<MemoryRouter><DunningBatch {...props} periodStart="2026-02-01" /></MemoryRouter>);
  view.rerender(<MemoryRouter><DunningBatch {...props} /></MemoryRouter>);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Auswahl bestätigen" })).toBeNull();
});

it("ignores a late preview after a session switch and after unmount", async () => {
  const view = show(); await prepare();
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  auth.session = { ...auth.session, organization_id: "other-org" };
  view.rerender(<MemoryRouter><DunningBatch {...props} /></MemoryRouter>);
  await act(async () => resolve(preview));
  expect(screen.queryByRole("checkbox")).toBeNull();
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeEnabled());
  vi.mocked(api.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  view.unmount(); await act(async () => resolve(preview));
  expect(api.post).toHaveBeenCalledTimes(3);
});

it("requires an explicit interest rate only when interest is requested", async () => {
  show(); await prepare();
  fireEvent.change(screen.getByLabelText("Verzugszinsen"), { target: { value: "yes" } });
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "-0,1250" } });
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
  expect(api.post).toHaveBeenLastCalledWith("/api/dunning/batch/preview", expect.objectContaining({ base_interest_rate: "-0.1250" }));
});

it("rejects malformed monetary rows visibly instead of showing a partial selectable preview", async () => {
  vi.mocked(api.post).mockResolvedValueOnce({ ...preview, candidates: [{ ...candidates[0], total_amount: "not money" }] });
  show(); fireEvent.click(screen.getByRole("button", { name: "Mahnlauf öffnen" }));
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Mindestrückstand (EUR) *"), { target: { value: "0.01" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnlauf prüfen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ungültiger Geldbetrag");
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Mahnlauf prüfen" }));
  await screen.findByRole("checkbox", { name: /Person 1/ });
});
