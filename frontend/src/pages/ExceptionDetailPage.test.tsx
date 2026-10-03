import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthCtx } from '../auth/useAuth';
import { ExceptionDetailPage } from './ExceptionDetailPage';
import { ExceptionsPage } from './ExceptionsPage';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const item = { id: 'case-1', document_ref_id: 'ref-1', logical_document_id: 'logical-1', extraction_revision_id: 'extraction-1', scope_id: 'scope-1',
  scope_label: 'WEG Lindenstraße', scope_type: 'WEG', cause: 'scope_hint_conflict', created_at: '2026-10-03T12:00:00Z', updated_at: '2026-10-03T12:00:00Z',
  revision_id: 'event-1', revision_number: 1, resolution_state: 'unresolved', workflow_state: 'open', claimant_id: null, snoozed_until: null, dependency_count: 1, can_work: true };
const event = { id: 'event-1', case_id: 'case-1', previous_event_id: null, revision_number: 1, action: 'evidence_added', resolution_state: 'unresolved', workflow_state: 'open', claimant_id: null,
  snoozed_until: null, reason: 'Evidence', actor_id: null, dependency_id: 'dep-1', created_at: item.created_at };
const dependency = { id: 'dep-1', case_id: 'case-1', assignment_proposal_id: 'proposal-1', extraction_revision_id: 'extraction-1', cause: item.cause, created_at: item.created_at, revision_number: 1, proposal_revision_number: 1, proposal_reason: 'Original scope needs comparison' };
function response(url: string, current = item) {
  if (url.includes('/duplicate-dependencies?')) return json({ items: [], next_before_revision: null });
  if (url.includes('/events?')) return json({ items: [event], next_before_revision: null });
  if (url.includes('/dependencies?')) return json({ items: [dependency], next_before_revision: null });
  return json(current);
}
function page(path = '/klaerungsfaelle/case-1') {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: 'actor-1', organization_id: 'org-1', role: 'CLERK' }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={[path]}><Routes><Route path="/klaerungsfaelle/:caseId" element={<ExceptionDetailPage />} /><Route path="/klaerungsfaelle" element={<ExceptionsPage />} /></Routes></MemoryRouter>
  </AuthCtx.Provider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('shows source, cause, immutable evidence and advisory actions without a resolve control', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url))); page();
  await screen.findByText('WEG Lindenstraße · WEG');
  expect(screen.getByRole('link', { name: 'Original und Zuordnungsvorschläge prüfen' })).toHaveAttribute('href', '/posteingang/belege/ref-1?logical_document_id=logical-1&extraction_revision_id=extraction-1');
  expect(screen.getByText(/Automatisch aus Vorschlag/)).toBeInTheDocument();
  expect(screen.getByText(/hebt die fachliche Sperre nicht auf/)).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /lösen|erledigen/i })).not.toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Meinen Anspruch freigeben' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Aktion speichern' })).toBeDisabled();
});

it('preserves an uncertain frozen snooze across transient read failure and a newer case version', async () => {
  let offline = false, advanced = false; const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    if (offline) return json({}, 503);
    return response(url, advanced ? { ...item, revision_id: 'event-2', revision_number: 2 } : item);
  }));
  page(); fireEvent.change(await screen.findByLabelText('Aktion'), { target: { value: 'snooze' } });
  fireEvent.change(screen.getByLabelText('Zurückstellen bis (Ortszeit)'), { target: { value: '2099-01-01T12:00' } });
  fireEvent.change(screen.getByLabelText('Begründung der Aktion'), { target: { value: 'Rückmeldung zum Original abwarten' } });
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  offline = true; fireEvent.click(screen.getByRole('button', { name: 'Fall aktualisieren' })); await screen.findByText(/Fall derzeit nicht verfügbar/);
  expect(screen.queryByLabelText('Begründung der Aktion')).not.toBeInTheDocument();
  offline = false; advanced = true; fireEvent.click(screen.getByRole('button', { name: 'Fall aktualisieren' }));
  await screen.findByText(/Offen · Fachlich ungeklärt · Version 2/);
  expect(screen.getByLabelText('Begründung der Aktion')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Auftrag wiederholen' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const header of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(header)).toBe(new Headers(requests[1].headers).get(header));
});

it('retains conflicting input and requires explicit adoption before sending a new version', async () => {
  let advanced = false; const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); advanced = true; return json({}, 409); }
    return response(url, advanced ? { ...item, revision_id: 'event-2', revision_number: 2 } : item);
  }));
  page(); fireEvent.change(await screen.findByLabelText('Begründung der Aktion'), { target: { value: 'Abgleich übernehmen' } });
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await screen.findByText(/Der Fall wurde geändert/);
  await screen.findByText(/Offen · Fachlich ungeklärt · Version 2/);
  expect(screen.getByLabelText('Begründung der Aktion')).toHaveValue('Abgleich übernehmen');
  expect(screen.getByRole('button', { name: 'Aktion speichern' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Aktuellen Fallstand übernehmen' }));
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(new Headers(requests[1].headers).get('If-Match')).toBe('"event-2"');
  expect(new Headers(requests[1].headers).get('Idempotency-Key')).not.toBe(new Headers(requests[0].headers).get('Idempotency-Key'));
});

it('clears source, history and uncertain draft after a fresh denial', async () => {
  let revoked = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : revoked ? json({}, 404) : response(url)));
  page(); fireEvent.change(await screen.findByLabelText('Begründung der Aktion'), { target: { value: 'Private note' } });
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  revoked = true; fireEvent.click(screen.getByRole('button', { name: 'Fall aktualisieren' })); await screen.findByText('Dieser Fall ist nicht mehr zugänglich.');
  expect(screen.queryByText('WEG Lindenstraße · WEG')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Bearbeitungshistorie')).not.toBeInTheDocument();
  revoked = false; fireEvent.click(screen.getByRole('button', { name: 'Fall aktualisieren' }));
  expect(await screen.findByLabelText('Begründung der Aktion')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Denselben Auftrag wiederholen' })).not.toBeInTheDocument();
});

it('does not infer current state from a historical successful receipt', async () => {
  const current = { ...item, revision_id: 'event-3', revision_number: 3 };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ ...event, id: 'event-2', revision_number: 2, action: 'snooze', workflow_state: 'snoozed', snoozed_until: '2099-01-01T12:00:00Z' }) : response(url, current)));
  page(); fireEvent.change(await screen.findByLabelText('Begründung der Aktion'), { target: { value: 'Recorded work' } });
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await screen.findByText(/Beleg für Fallversion 2/);
  await screen.findByText(/Offen · Fachlich ungeklärt · Version 3/);
});

it('rejects a past local snooze before sending and offers only read access when paused', async () => {
  const fetch = vi.fn(async (url: string) => response(url)); vi.stubGlobal('fetch', fetch); page();
  fireEvent.change(await screen.findByLabelText('Aktion'), { target: { value: 'snooze' } });
  fireEvent.change(screen.getByLabelText('Zurückstellen bis (Ortszeit)'), { target: { value: '2020-01-01T12:00' } });
  fireEvent.change(screen.getByLabelText('Begründung der Aktion'), { target: { value: 'Wait' } });
  fireEvent.click(screen.getByRole('button', { name: 'Aktion speichern' })); await screen.findByText(/zukünftigen Zeitpunkt/);
  expect(fetch.mock.calls).toHaveLength(4);
  cleanup(); vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url, { ...item, can_work: false }))); page();
  await screen.findByText('Dieser Fall ist derzeit nur lesbar.');
  expect(screen.queryByLabelText('Begründung der Aktion')).not.toBeInTheDocument();
});

it('paginates histories and clears all evidence on denied continuation', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('before_revision=3')) return denied ? json({}, 404) : json({ items: [event], next_before_revision: null });
    if (url.includes('/events?')) return json({ items: [{ ...event, id: 'event-3', revision_number: 3 }], next_before_revision: 3 });
    if (url.includes('/dependencies?')) return json({ items: [{ ...dependency, revision_number: 3 }], next_before_revision: 3 });
    return response(url);
  }));
  page(); fireEvent.click(await screen.findByRole('button', { name: 'Ältere Ereignisse' })); await screen.findByText(/Weiterer Zuordnungsvorschlag erfasst · Version 1/);
  expect(screen.queryByText(/Weiterer Zuordnungsvorschlag erfasst · Version 3/)).not.toBeInTheDocument();
  denied = true; fireEvent.click(screen.getByRole('button', { name: 'Ältere Verknüpfungen' })); await screen.findByText(/Historie derzeit nicht verfügbar/);
  expect(screen.queryByLabelText('Fallgrundlage')).not.toBeInTheDocument();
});

it('replaces queue pages, binds the filter and drops old entries after a failed refresh', async () => {
  let offline = false; const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url); if (offline) return json({}, 503);
    return url.includes('cursor=next') ? json({ items: [{ ...item, id: 'case-2', scope_label: 'MIET Gartenstraße' }], next_cursor: null })
      : json({ items: [item], next_cursor: 'next' });
  }));
  page('/klaerungsfaelle'); await screen.findByText('WEG Lindenstraße · WEG');
  fireEvent.click(screen.getByRole('button', { name: 'Nächste Fälle' })); await screen.findByText('MIET Gartenstraße · WEG');
  expect(screen.queryByText('WEG Lindenstraße · WEG')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Bearbeitungsstand'), { target: { value: 'snoozed' } }); await screen.findByText('WEG Lindenstraße · WEG');
  expect(urls.at(-1)).toContain('workflow_state=snoozed'); expect(urls.at(-1)).not.toContain('cursor=');
  offline = true; fireEvent.click(screen.getByRole('button', { name: 'Fälle aktualisieren' })); await screen.findByText(/Klärungsfälle derzeit nicht verfügbar/);
  expect(screen.queryByText('WEG Lindenstraße · WEG')).not.toBeInTheDocument();
});

it('shows typed duplicate evidence, exact sources and the stored replacement direction', async () => {
  const duplicate = { id: 'dup-1', case_id: item.id, scope_id: item.scope_id, anchor_review_id: 'review-a', reference_review_id: 'review-b',
    anchor_document_ref_id: 'source-a', anchor_logical_id: 'logical-a', anchor_extraction_id: 'extraction-a', reference_document_ref_id: 'source-b', reference_logical_id: 'logical-b', reference_extraction_id: 'extraction-b',
    anchor_seller_name: 'Seller A', anchor_document_number: 'R-A', reference_seller_name: 'Seller B', reference_document_number: 'R-B', created_at: item.created_at, revision_number: 1,
    evidence: { match_reasons: ['same_reviewed_identity'], kind_conflict: false }, latest_exact_comparison: { subject_review_id: 'review-b', reference_review_id: 'review-a', verdict: 'replacement_requested', reason: 'Corrected source compared' } };
  const current = { ...item, cause: 'duplicate_candidate', dependency_count: 0, duplicate_dependency_count: 1 };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/duplicate-dependencies?')) return json({ items: [duplicate], next_before_revision: null });
    if (url.includes('/events?')) return json({ items: [{ ...event, action: 'duplicate_evidence_added' }, { ...event, id: 'event-2', action: 'duplicate_comparison_recorded' }], next_before_revision: null });
    if (url.includes('/dependencies?')) return json({ items: [], next_before_revision: null });
    return json(current);
  }));
  page(); await screen.findByText('Mögliche Dubletten vergleichen');
  expect(screen.getByRole('link', { name: 'Beleg B am Original prüfen' })).toHaveAttribute('href', '/posteingang/belege/source-b?logical_document_id=logical-b&extraction_revision_id=extraction-b');
  expect(screen.getByText('Letzter Vergleich dieser Prüfstände: Beleg B soll Beleg A ersetzen.')).toBeInTheDocument();
  expect(screen.getByText(/keine Kostenfreigabe und kein Abschluss/)).toBeInTheDocument(); expect(screen.getAllByText(/Automatisch verknüpft/)).toHaveLength(2);
});

it('removes the whole case when a typed duplicate dependency loses its source audience', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => denied && url.includes('/duplicate-dependencies?') ? json({}, 404) : response(url)));
  page(); await screen.findByText('WEG Lindenstraße · WEG'); denied = true;
  fireEvent.click(screen.getByRole('button', { name: 'Fall aktualisieren' })); await screen.findByText('Dieser Fall ist nicht mehr zugänglich.');
  expect(screen.queryByText('WEG Lindenstraße · WEG')).not.toBeInTheDocument(); expect(screen.queryByLabelText('Aktion')).not.toBeInTheDocument();
});
