import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContextCandidatesPanel } from './ContextCandidatesPanel';
import type { ContextCandidates } from '../../api/contextCandidates';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const evidence = { kind: 'property_id' as const, value: 'property', page_number: 2, quotation: 'Objekt-ID: property', method: 'text_layer' as const };
const value: ContextCandidates = { logical_document_id: 'logical', partition_revision_id: 'partition', extraction_revision_id: 'extraction', status: 'manual_review', can_prepare: true, conflicting_properties: false, ambiguous_scopes: false, period_basis: 'unreviewed_extraction', unmatched_evidence: [],
  items: [{ scope_id: 'scope', scope_label: 'WEG Test', scope_type: 'WEG', property_id: 'property', property_name: 'Test property', evidence: [evidence], exclusions: [], requires_manual_review: true, meter_period_unverified: false }] };
const props = { logicalId: 'logical', partitionId: 'partition', extractionId: 'extraction', disabled: false, parentGeneration: 0, onPick: vi.fn() };
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('shows exact evidence and copies only after an explicit action', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(value))); render(<ContextCandidatesPanel {...props} />);
  await screen.findByText(/Seite 2: „Objekt-ID/); expect(props.onPick).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Als manuelle Auswahl übernehmen: WEG Test' }));
  expect(props.onPick).toHaveBeenCalledWith(value.items[0]);
});
it('keeps alternatives, unmatched assertions and provisional exclusions visible', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...value, conflicting_properties: true, ambiguous_scopes: true,
    unmatched_evidence: [{ ...evidence, quotation: 'Objekt-ID: unknown' }], items: [{ ...value.items[0], meter_period_unverified: true, exclusions: ['period_outside_scope'] }] })));
  render(<ContextCandidatesPanel {...props} />); await screen.findByText(/Mehrere Objekte/);
  expect(screen.getByText(/Objekt-ID: unknown/)).toBeInTheDocument(); expect(screen.getByText(/keine Gültigkeit im Leistungszeitraum/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Als manuelle Auswahl übernehmen: WEG Test' })).toBeDisabled();
});
it('disables selection during an unknown parent command or stale source basis', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(value)));
  const rendered = render(<ContextCandidatesPanel {...props} disabled />); await screen.findByText(/Seite 2/);
  expect(screen.getByRole('button', { name: 'Als manuelle Auswahl übernehmen: WEG Test' })).toBeDisabled();
  rendered.rerender(<ContextCandidatesPanel {...props} extractionId="new" parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Als manuelle Auswahl übernehmen: WEG Test' })).not.toBeInTheDocument());
  expect(props.onPick).not.toHaveBeenCalled();
});
it('clears denied evidence without invoking or overwriting the parent draft', async () => {
  let denied = false; vi.stubGlobal('fetch', vi.fn(async () => denied ? json({}, 404) : json(value)));
  render(<ContextCandidatesPanel {...props} />); await screen.findByText(/Seite 2/);
  denied = true; fireEvent.click(screen.getByRole('button', { name: 'Zuordnungshinweise aktualisieren' }));
  await screen.findByText(/Ihre manuelle Eingabe bleibt erhalten/);
  expect(screen.queryByText(/Seite 2/)).not.toBeInTheDocument(); expect(props.onPick).not.toHaveBeenCalled();
});
