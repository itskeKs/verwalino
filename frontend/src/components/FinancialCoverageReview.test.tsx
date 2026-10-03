import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FinancialCoverageReview } from "./FinancialCoverageReview";

const json = (body: unknown, status = 201) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("requires reason and explicit completeness for zero, and rechecks after an edit", async () => {
  const fetchMock = vi.fn(async () => json({ id: "evidence-1" }));
  vi.stubGlobal("fetch", fetchMock);
  const props = { kind: "settlement" as const, subjectId: "run-1", values: { advance_payments_total: "0.00" } };
  const view = render(<FinancialCoverageReview {...props} />);
  const save = screen.getByRole("button", { name: "Datengrundlage speichern" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Complete bank statements checked" } });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(save);
  await screen.findByRole("status");
  const init = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0][1];
  expect(JSON.parse(init.body as string)).toMatchObject({ values: { advance_payments_total: "0.00" }, confirmed_complete: true });
  view.rerender(<FinancialCoverageReview {...props} values={{ advance_payments_total: "100.00" }} />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(save).toBeDisabled();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("keeps review text after stale derived input and explains recovery", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: { code: "financial_inputs_incomplete", blockers: [{ code: "derived_total_stale" }] } }, 422)));
  render(<FinancialCoverageReview kind="settlement" subjectId="run-1" values={{ advance_payments_total: null }} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Retain review note" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Datengrundlage speichern" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("zuerst neu aus den Zahlungen ermitteln"));
  expect(screen.getByRole("textbox")).toHaveValue("Retain review note");
  expect(screen.queryByRole("status")).toBeNull();
});

it('records explicitly selected source versions and resets confirmation on source or reason changes', async () => {
  const posts: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(options); return json({ id: 'coverage' }); }
    return json({ items: [{ id: 'source-v2', document_number: 'Reviewed invoice', original_filename: 'source.pdf', version: 2 }], next_cursor: null }, 200);
  }));
  render(<FinancialCoverageReview kind="settlement" subjectId="run" values={{ advance_payments_total: '0.00' }} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed original source' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Freigegebene Belege auswählen' }));
  const source = await screen.findByLabelText('Reviewed invoice · Version 2'); fireEvent.click(source);
  const save = screen.getByRole('button', { name: 'Datengrundlage speichern' }); expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Alle angezeigten Beträge/)); expect(save).toBeEnabled();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed source and current period' } }); expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Alle angezeigten Beträge/)); fireEvent.click(save);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(JSON.parse(posts[0].body as string).source_document_ids).toEqual(['source-v2']);
  expect(screen.getByRole('link', { name: 'Beleg öffnen' })).toHaveAttribute('href', '/belege/source-v2');
});

it.each(['replacement', 'denied'])('clears obsolete selected evidence and acknowledgment during %s refresh', async outcome => {
  let calls = 0; let resolveRefresh: ((value: Response) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn(async () => {
    if (calls++ === 0) return json({ items: [{ id: 'v1', document_number: 'Old source', version: 1 }], next_cursor: null }, 200);
    return new Promise<Response>(resolve => { resolveRefresh = resolve; });
  }));
  render(<FinancialCoverageReview kind="settlement" subjectId="run" values={{ advance_payments_total: '0.00' }} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed full period' } });
  fireEvent.click(screen.getByRole('button', { name: 'Freigegebene Belege auswählen' }));
  fireEvent.click(await screen.findByLabelText('Old source · Version 1')); fireEvent.click(screen.getByLabelText(/Alle angezeigten Beträge/));
  const save = screen.getByRole('button', { name: 'Datengrundlage speichern' }); expect(save).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Freigegebene Belege auswählen' }));
  expect(save).toBeDisabled(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.getByLabelText(/Alle angezeigten Beträge/)).not.toBeChecked();
  resolveRefresh!(outcome === 'denied' ? json({}, 403) : json({ items: [{ id: 'v2', document_number: 'New source', version: 2 }], next_cursor: null }, 200));
  if (outcome === 'denied') { await screen.findByRole('alert'); expect(screen.queryByRole('link')).not.toBeInTheDocument(); }
  else expect(await screen.findByLabelText('New source · Version 2')).not.toBeChecked();
  expect(save).toBeDisabled(); expect(screen.queryByText(/Belegnachweise ausgewählt/)).not.toBeInTheDocument();
});

it.each([200, 403])('keeps validated selections across pagination only while access remains valid (%s)', async code => {
  let calls = 0;
  vi.stubGlobal('fetch', vi.fn(async () => calls++ === 0
    ? json({ items: [{ id: 'v1', document_number: 'First source', version: 1 }], next_cursor: 'v1' }, 200)
    : json({ items: [{ id: 'v2', document_number: 'Second source', version: 1 }], next_cursor: null }, code)));
  render(<FinancialCoverageReview kind="settlement" subjectId="run" values={{ advance_payments_total: '0.00' }} />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed full period' } });
  fireEvent.click(screen.getByRole('button', { name: 'Freigegebene Belege auswählen' }));
  fireEvent.click(await screen.findByLabelText('First source · Version 1')); fireEvent.click(screen.getByLabelText(/Alle angezeigten Beträge/));
  fireEvent.click(screen.getByRole('button', { name: 'Weitere Belege laden' }));
  const save = screen.getByRole('button', { name: 'Datengrundlage speichern' });
  if (code === 200) {
    await screen.findByLabelText('Second source · Version 1'); expect(screen.getByLabelText('First source · Version 1')).toBeChecked(); expect(save).toBeEnabled();
  } else {
    await screen.findByRole('alert'); expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(screen.queryByText(/First source/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Alle angezeigten Beträge/)).not.toBeChecked(); expect(save).toBeDisabled();
  }
});
