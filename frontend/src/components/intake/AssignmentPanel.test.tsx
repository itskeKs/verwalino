import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AssignmentPanel } from './AssignmentPanel';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const scope = { id: 'scope', label: 'Objekt Nord', scope_type: 'WEG' };
const proposal = { id: 'proposal', partition_revision_id: 'partition', extraction_revision_id: 'extraction', scope_id: 'scope', scope_label: 'Objekt Nord', scope_type: 'WEG', revision_number: 1, state: 'blocked', blockers: ['scope_hint_conflict'], reason: 'Existing manual choice' };
function respond(url: string) {
  if (url.endsWith('/partition')) return json({ current: { id: 'partition', logical_document_ids: ['logical'] }, can_review: true, can_export: false });
  if (url.includes('/assignment-proposals')) return json({ items: [], next_before_revision: null });
  return json({ items: [scope], next_cursor: null });
}
function page() { return render(<AssignmentPanel logicalId="logical" refId="ref" extractionId="extraction" evidenceReady parentBusy={false} parentGeneration={0} onDenied={vi.fn()} />); }
async function fill() {
  await screen.findByRole('option', { name: 'Objekt Nord · WEG' });
  fireEvent.change(screen.getByLabelText('Vorgeschlagener Abrechnungskreis'), { target: { value: 'scope' } });
  fireEvent.change(screen.getByLabelText('Begründung des Zuordnungsvorschlags'), { target: { value: 'Original manuell gesichtet' } });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('submits manual context against exact evidence and shows its unresolved blocker', async () => {
  let request: RequestInit | undefined; let saved = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { request = init; saved = true; return json(proposal, 201); }
    return saved && url.includes('/assignment-proposals') ? json({ items: [proposal], next_before_revision: null }) : respond(url);
  }));
  page(); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung vorschlagen' }));
  await screen.findByText('Upload-Kontext und vorgeschlagener Abrechnungskreis weichen voneinander ab.');
  expect(JSON.parse(String(request?.body))).toEqual({ partition_revision_id: 'partition', extraction_revision_id: 'extraction', scope_id: 'scope', reason: 'Original manuell gesichtet' });
  expect(new Headers(request?.headers).get('If-Match')).toBe('"none"');
  expect(screen.getByText('Objekt Nord · WEG', { selector: 'p' })).toBeInTheDocument();
});

it('preserves a frozen assignment through a failed fresh read', async () => {
  const requests: RequestInit[] = []; let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    return offline && url.includes('/assignment-proposals') ? json({}, 503) : respond(url);
  }));
  page(); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })); await screen.findByText(/Keine bestätigte Antwort/);
  offline = true; fireEvent.click(screen.getByRole('button', { name: 'Zuordnung aktualisieren' })); await screen.findByText(/Zuordnung derzeit nicht verfügbar/);
  offline = false; fireEvent.click(screen.getByRole('button', { name: 'Zuordnung aktualisieren' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Zuordnungsvorschlag erneut senden' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Zuordnungsvorschlag erneut senden' }));
  await waitFor(() => expect(requests).toHaveLength(2));expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('retains a stale draft until explicit adoption and never chooses a scope automatically', async () => {
  let changed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { changed = true; return json({}, 409); }
    return changed && url.includes('/assignment-proposals') ? json({ items: [proposal], next_before_revision: null }) : respond(url);
  }));
  page(); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })); await screen.findByText(/Bearbeitungsstand hat sich geändert/);
  expect(screen.getByLabelText('Begründung des Zuordnungsvorschlags')).toHaveValue('Original manuell gesichtet');
  expect(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Aktuelle Grundlage übernehmen' }));
  expect(screen.getByLabelText('Vorgeschlagener Abrechnungskreis')).toHaveValue('');
});


it('does not submit a child outside the current reviewed partition', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/partition')
    ? json({ current: { id: 'partition', logical_document_ids: ['other'] }, can_review: true }) : respond(url)));
  page(); await screen.findByText('Dieser Beleg gehört noch nicht zur aktuellen geprüften Aufteilung.');
  expect(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })).toBeDisabled();
});

it('clears destination details on fresh source denial', async () => {
  let revoked = false; const denied = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => revoked && url.endsWith('/partition') ? json({}, 404)
    : url.includes('/assignment-proposals') ? json({ items: [proposal], next_before_revision: null }) : respond(url)));
  render(<AssignmentPanel logicalId="logical" refId="ref" extractionId="extraction" evidenceReady parentBusy={false} parentGeneration={0} onDenied={denied} />);
  await screen.findByText('Existing manual choice'); revoked = true;
  fireEvent.click(screen.getByRole('button', { name: 'Zuordnung aktualisieren' }));
  await waitFor(() => expect(denied).toHaveBeenCalledOnce());
  expect(screen.queryByText('Existing manual choice')).not.toBeInTheDocument();
});

it('ignores an old assignment response after changing source', async () => {
  let finish: ((value: Response) => void) | undefined; const denied = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST'
    ? await new Promise<Response>(resolve => { finish = resolve; }) : respond(url)));
  const view = render(<AssignmentPanel logicalId="logical" refId="ref" extractionId="extraction" evidenceReady parentBusy={false} parentGeneration={0} onDenied={denied} />);
  await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })); await waitFor(() => expect(finish).toBeDefined());
  view.unmount(); finish!(json({}, 404)); await Promise.resolve(); await Promise.resolve(); expect(denied).not.toHaveBeenCalled();
});


it('waits for initial extraction evidence without treating normal loading as an edit conflict', async () => {
  const denied = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => respond(url)));
  const view = render(<AssignmentPanel logicalId="logical" refId="ref" extractionId={null} evidenceReady={false} parentBusy={false} parentGeneration={0} onDenied={denied} />);
  await screen.findByText('Noch kein lesbarer Zuordnungsvorschlag.');
  view.rerender(<AssignmentPanel logicalId="logical" refId="ref" extractionId="extraction" evidenceReady parentBusy={false} parentGeneration={0} onDenied={denied} />);
  await fill();
  expect(screen.queryByRole('button', { name: 'Aktuelle Grundlage übernehmen' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Zuordnung vorschlagen' })).not.toBeDisabled();
});
