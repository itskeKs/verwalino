import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { PersonsPage } from "./PersonsPage";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("keeps the person list mounted during a saved edit refresh", async () => {
  let finish!: (value: Response) => void;
  let saved = false;
  const person = { id: "person", first_name: "Anna", last_name: "Beispiel", street: "", zip_code: "", city: "", can_prepare: true };
  vi.stubGlobal("fetch", vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === "PUT") { saved = true; return json({ ...person, last_name: "Neu" }); }
    return saved ? new Promise<Response>(resolve => { finish = resolve; }) : json({ items: [person], next_cursor: null });
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "CLERK" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><PersonsPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole("link", { name: "Anna Beispiel" });
  const table = screen.getByRole("table");
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Nachname *"), { target: { value: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Person gespeichert.");
  expect(table).toBeInTheDocument();
  await act(async () => finish(json({ items: [{ ...person, last_name: "Neu" }], next_cursor: null })));
  expect(await screen.findByRole("link", { name: "Anna Neu" })).toBeVisible();
});


it.each([401, 403, 500])("handles an open private person editor when the retained-list refresh returns %s", async status => {
  let refresh!: (value: Response) => void;
  let saved = false;
  const persons = ['A', 'B'].map(id => ({ id, first_name: 'Kontakt', last_name: id, street: '', zip_code: '', city: '', can_prepare: true,
    email: `privat-${id}@example.invalid`, iban: 'DE89370400440532013000' }));
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === 'PUT') { saved = true; return json(persons[0]); }
    return saved ? new Promise<Response>(resolve => { refresh = resolve; }) : json({ items: persons, next_cursor: null });
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: 'u', organization_id: 'o', role: 'CLERK' }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><PersonsPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole('link', { name: 'Kontakt A' });
  fireEvent.click(within(screen.getByRole('link', { name: 'Kontakt A' }).closest('tr')!).getByRole('button', { name: 'Bearbeiten' }));
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(refresh).toBeTypeOf('function'));
  fireEvent.click(within(screen.getByRole('link', { name: 'Kontakt B' }).closest('tr')!).getByRole('button', { name: 'Bearbeiten' }));
  fireEvent.change(screen.getByLabelText('Telefon'), { target: { value: '030 987654' } });
  await act(async () => refresh(json({ detail: 'Zugriff auf Kontakte nicht verfügbar' }, status)));
  await screen.findByText(/Zugriff auf Kontakte nicht verfügbar/);
  if (status === 500) {
    expect(screen.getByLabelText('E-Mail')).toHaveValue('privat-B@example.invalid');
    expect(screen.getByLabelText('Telefon')).toHaveValue('030 987654');
  } else {
    expect(screen.queryByLabelText('E-Mail')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue('DE89370400440532013000')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Kontakt B' })).not.toBeInTheDocument();
  }
});

it.each([401, 403])("invalidates an outstanding person save after the list confirms access loss %s", async status => {
  let refresh!: (value: Response) => void;
  let secondSave!: (value: Response) => void;
  let writes = 0;
  const persons = ['A', 'B'].map(id => ({ id, first_name: 'Kontakt', last_name: id, street: '', zip_code: '', city: '', can_prepare: true, email: `privat-${id}@example.invalid` }));
  const fetchMock = vi.fn(async (_url: string, options?: RequestInit) => {
    if (options?.method === 'PUT') { writes++; return writes === 1 ? json(persons[0]) : new Promise<Response>(resolve => { secondSave = resolve; }); }
    return writes ? new Promise<Response>(resolve => { refresh = resolve; }) : json({ items: persons, next_cursor: null });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: 'u', organization_id: 'o', role: 'CLERK' }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><PersonsPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole('link', { name: 'Kontakt A' });
  for (const id of ['A', 'B']) {
    fireEvent.click(within(screen.getByRole('link', { name: `Kontakt ${id}` }).closest('tr')!).getByRole('button', { name: 'Bearbeiten' }));
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
    if (id === 'A') await waitFor(() => expect(refresh).toBeTypeOf('function'));
  }
  await act(async () => refresh(json({ detail: 'Zugriff entzogen' }, status)));
  await screen.findByText(/Zugriff entzogen/);
  const reads = fetchMock.mock.calls.filter(([, options]) => !options?.method).length;
  await act(async () => secondSave(json(persons[1])));
  expect(screen.queryByLabelText('E-Mail')).not.toBeInTheDocument();
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, options]) => !options?.method)).toHaveLength(reads);
});
