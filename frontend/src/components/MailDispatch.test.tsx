import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthCtx } from "../auth/useAuth";
import { MailDispatch } from "./MailDispatch";
import type { MailKind } from "../api/mail";

const recipients = [{ person_id: "p1", name: "Anna Beispiel", email: "anna@example.test" }, { person_id: "p2", name: "Ohne Adresse", email: null }];
const attempt = { id: "a1", person_id: "p1", email: "anna@example.test", status: "accepted", created_at: "2026-10-21T12:00:00Z", completed_at: "2026-10-21T12:00:01Z" };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function view(role = "OWNER_ADMIN", documentId = "doc1", kind: MailKind = "dunning") {
  return <AuthCtx.Provider value={{ loading: false, session: { user_id: "u1", organization_id: "o1", role }, login: vi.fn(), logout: vi.fn() }}><MailDispatch kind={kind} documentId={documentId} /></AuthCtx.Provider>;
}
function select(personId = "p1") { fireEvent.change(screen.getByLabelText("E-Mail-Empfänger"), { target: { value: personId } }); }
const send = () => screen.getByRole("button", { name: "E-Mail-Versand bestätigen" });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(["OWNER_ADMIN", "CLERK"])("requires explicit selection and email, confirms local dispatch for %s", async role => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => options?.method === "POST" ? json(attempt) : json({ recipients, attempts: [] }));
  vi.stubGlobal("fetch", fetch);
  render(view(role));
  await screen.findByLabelText("E-Mail-Empfänger");
  expect(send()).toBeDisabled(); select("p2"); expect(send()).toBeDisabled();
  select(); expect(screen.getByText("E-Mail: anna@example.test")).toBeVisible();
  fireEvent.click(send()); expect(fetch).toHaveBeenCalledTimes(1);
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/Anna Beispiel.*anna@example.test.*lokal.*kein.*Zustellnachweis/s));
  confirm.mockReturnValue(true); fireEvent.click(send());
  await waitFor(() => expect(fetch.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true));
  const [url, options] = fetch.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(url).toBe("/api/mail/dunning/doc1");
  expect(JSON.parse(options!.body as string)).toEqual({ person_id: "p1", request_id: expect.any(String) });
  expect(options?.credentials).toBe("include");
});

it("allows read-only history without send controls and labels outcomes honestly", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ recipients, attempts: [attempt, { ...attempt, id: "a2", status: "failed" }, { ...attempt, id: "a3", status: "unknown" }, { ...attempt, id: "a4", status: "prepared" }] })));
  render(view("READ_ONLY"));
  expect(await screen.findByText("SMTP angenommen (kein Zustellnachweis)")).toBeVisible();
  expect(screen.getByText("Fehlgeschlagen")).toBeVisible();
  expect(screen.getByText("Ausgang unklar – vor erneutem Versand prüfen (vorbereitet)")).toBeVisible();
  expect(screen.getAllByText("Ausgang unklar – vor erneutem Versand prüfen").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: /Versand bestätigen/ })).toBeNull();
  expect(screen.queryByLabelText("E-Mail-Empfänger")).toBeNull();
});

it.each(["prepared", "unknown"])("blocks a new attempt for existing %s status", async status => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ recipients, attempts: [{ ...attempt, status, completed_at: null }] })));
  render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select(); expect(send()).toBeDisabled();
});

it("shows HTTP loading errors and reloads before permitting sends", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ detail: "Kein Zugriff" }, 403)).mockResolvedValue(json({ recipients, attempts: [] })));
  render(view()); expect(await screen.findByRole("alert")).toHaveTextContent("Kein Zugriff");
  fireEvent.click(screen.getByRole("button", { name: "Versandhistorie neu laden" }));
  await screen.findByLabelText("E-Mail-Empfänger"); select(); expect(send()).toBeEnabled();
});

it("preserves request_id on an explicit network retry and locks double submits", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let reject!: (error: Error) => void;
  let posts = 0;
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method !== "POST") return json({ recipients, attempts: [] });
    posts++;
    if (posts === 1) return new Promise<Response>((_, no) => { reject = no; });
    return json(attempt);
  });
  vi.stubGlobal("fetch", fetch); render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select();
  const button = send(); fireEvent.click(button); fireEvent.click(button); expect(posts).toBe(1);
  await act(async () => reject(new TypeError("Netz getrennt")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Netz getrennt");
  await waitFor(() => expect(screen.getByRole("button", { name: "Denselben Versandversuch wiederholen" })).toBeEnabled());
  expect(fetch.mock.calls.filter(([, options]) => !options?.method).length).toBe(2);
  fireEvent.click(screen.getByRole("button", { name: "Denselben Versandversuch wiederholen" }));
  await waitFor(() => expect(posts).toBe(2));
  const bodies = fetch.mock.calls.filter(([, options]) => options?.method === "POST").map(([, options]) => JSON.parse(options!.body as string));
  expect(bodies[1]).toEqual(bodies[0]);
});

it("refetches network failures and prevents retry when the observed outcome is unresolved", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let posted = false;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "POST") { posted = true; throw new TypeError("Netzfehler"); }
    return json({ recipients, attempts: posted ? [{ ...attempt, status: "unknown", completed_at: null }] : [] });
  }));
  render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select(); fireEvent.click(send());
  await screen.findAllByText("Ausgang unklar – vor erneutem Versand prüfen");
  expect(screen.getByRole("button", { name: "Denselben Versandversuch wiederholen" })).toBeDisabled();
});

it.each(["accepted", "failed"])("uses a fresh request_id only for an explicit new completed %s attempt", async status => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let posts = 0;
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "POST") { posts++; return json({ ...attempt, status }); }
    return json({ recipients, attempts: posts ? [{ ...attempt, status }] : [] });
  });
  vi.stubGlobal("fetch", fetch); render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select(); fireEvent.click(send());
  await waitFor(() => expect(send()).toBeEnabled()); fireEvent.click(send());
  await waitFor(() => expect(posts).toBe(2));
  const bodies = fetch.mock.calls.filter(([, options]) => options?.method === "POST").map(([, options]) => JSON.parse(options!.body as string));
  expect(bodies[0].request_id).not.toBe(bodies[1].request_id);
});

it("discards stale document loads after navigation", async () => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url: string) => url.endsWith("doc1") ? new Promise<Response>(yes => { resolve = yes; }) : Promise.resolve(json({ recipients: [{ ...recipients[0], name: "Neues Dokument" }], attempts: [] }))));
  const { rerender } = render(view()); rerender(view("OWNER_ADMIN", "doc2"));
  await screen.findByRole("option", { name: "Neues Dokument" });
  await act(async () => resolve(json({ recipients, attempts: [attempt] })));
  expect(screen.queryByRole("option", { name: "Anna Beispiel" })).toBeNull();
  expect(send()).toBeDisabled();
});

it.each(["prepared", "unknown"])("never retries or creates a new attempt automatically after POST returns %s", async status => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const fetch = vi.fn(async (_url: string, options?: RequestInit) => options?.method === "POST"
    ? json({ ...attempt, status, completed_at: null }) : json({ recipients, attempts: [] }));
  vi.stubGlobal("fetch", fetch); render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select(); fireEvent.click(send());
  await screen.findAllByText("Ausgang unklar – vor erneutem Versand prüfen");
  await waitFor(() => expect(screen.queryByText("Versand läuft...")).toBeNull());
  expect(send()).toBeDisabled();
  expect(fetch.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
});

it("blocks stale history after a refresh failure until a successful explicit reload", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ recipients, attempts: [] }))
    .mockResolvedValueOnce(json({ detail: "Historie nicht verfügbar" }, 503))
    .mockResolvedValueOnce(json({ recipients, attempts: [] }));
  vi.stubGlobal("fetch", fetch); render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select();
  fireEvent.click(screen.getByRole("button", { name: "Versandhistorie neu laden" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503: Historie nicht verfügbar");
  expect(screen.getByText(/möglicherweise veraltet/)).toBeVisible(); expect(send()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Versandhistorie neu laden" }));
  await waitFor(() => expect(send()).toBeEnabled()); expect(screen.queryByRole("alert")).toBeNull();
});

it("ignores late POST responses after switching documents", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => options?.method === "POST"
    ? new Promise<Response>(yes => { resolve = yes; }) : json({ recipients, attempts: [] })));
  const { rerender } = render(view()); await screen.findByLabelText("E-Mail-Empfänger"); select(); fireEvent.click(send());
  rerender(view("OWNER_ADMIN", "doc2")); await screen.findByLabelText("E-Mail-Empfänger");
  await act(async () => resolve(json(attempt)));
  expect(screen.queryByText("SMTP angenommen (kein Zustellnachweis)")).toBeNull();
  expect(screen.getByLabelText("E-Mail-Empfänger")).toHaveValue(""); expect(send()).toBeDisabled();
});


it.each(["dunning", "settlement", "invitation"] as const)("links each %s attempt to its stored PDF, including for read-only users", async kind => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ recipients, attempts: [attempt, { ...attempt, id: "a2", status: "prepared", completed_at: null }] })));
  render(view("READ_ONLY", "doc1", kind));
  const links = await screen.findAllByRole("link", { name: "Versand-PDF öffnen" });
  expect(links).toHaveLength(2);
  expect(links.map(link => link.getAttribute("href"))).toEqual([
    `/api/mail/${kind}/doc1/a1/pdf`, `/api/mail/${kind}/doc1/a2/pdf`,
  ]);
  for (const link of links) {
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener");
  }
});
