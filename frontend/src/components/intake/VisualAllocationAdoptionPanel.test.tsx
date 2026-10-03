import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationAdoptionPanel } from './AllocationAdoptionPanel';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const artifact = { id: 'artifact', page_start: 1, page_end: 2, created_at: '2026-10-03T10:00:00Z', content_sha256: 'a'.repeat(64) };
const disclosure = { receipt: { id: 'publication', kind: 'published', created_at: artifact.created_at }, current: { id: 'publication', available: true, can_download: true }, destination: { label: 'MIET Review' } };
const preview = { can_adopt: true, preview_digest: 'visual-digest', source_variant: 'visual', visual_content_sha256: artifact.content_sha256, output_page_count: 2, scope_label: 'MIET Review', cost_count: 2, gross_amount: '119.00' };
const props = { logicalId: 'logical', reviewId: 'review', ready: false, parentBusy: false, parentGeneration: 0 };
const result = { id: 'set', kind: 'intake_reviewed', source_variant: 'visual', source_document_id: 'document', cost_count: 2, confirmed_at: artifact.created_at, costs: [], reason: 'Retain this reviewed source' };
async function fill() {
  await waitFor(() => expect(screen.getByRole('radio', { name: 'Freigegebener Auszug' })).toBeEnabled());
  fireEvent.click(screen.getByRole('radio', { name: 'Freigegebener Auszug' }));
  await screen.findByRole('option', { name: /Seiten 1–2/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Geprüfter Auszug' }), { target: { value: 'artifact' } });
  await screen.findByRole('option', { name: /MIET Review/ });
  fireEvent.change(screen.getByRole('combobox', { name: 'Freigabe im Abrechnungskreis' }), { target: { value: 'publication' } });
  fireEvent.click(screen.getByRole('button', { name: 'Übernahme mit Auszug prüfen' }));
  await screen.findByLabelText('Begründung der Übernahme mit Auszug');
  fireEvent.change(screen.getByLabelText('Begründung der Übernahme mit Auszug'), { target: { value: 'Retain this reviewed source' } });
  for (const checkbox of screen.getAllByRole('checkbox')) fireEvent.click(checkbox);
}
function response(url: string, committed = false) {
  if (url.includes('visual-adoption-preview')) return json(preview);
  if (url.includes('adoption-preview')) return json({ can_adopt: false });
  if (url.includes('allocation-adoptions')) return json({ item: committed ? result : null });
  if (url.includes('/disclosures')) return json({ items: [disclosure], next_cursor: null });
  if (url.includes('/visual-extracts')) return json({ items: [artifact], next_cursor: null });
  return json({ options: [], items: [] });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('offers visual adoption for a partial logical document and binds the exact reviewed publication and hash', async () => {
  const posts: { url: string; options: RequestInit }[] = []; let committed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
    if (options.method === 'POST') { posts.push({ url, options }); committed = true; return json(result, 201); }
    return response(url, committed);
  }));
  render(<AllocationAdoptionPanel {...props} />); await fill();
  expect(screen.getByRole('link', { name: /Genau diesen freigegebenen Auszug/ })).toHaveAttribute('href', '/api/visual-disclosures/publication/content');
  fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Auszug aufbewahren' }));
  await screen.findByText('Kostenübernahme bestätigt');
  expect(posts).toHaveLength(1); expect(posts[0].url).toBe('/api/logical-documents/logical/visual-allocation-adoptions');
  expect(JSON.parse(String(posts[0].options.body))).toEqual({ treatment_review_id: 'review', disclosure_id: 'publication', visual_content_sha256: artifact.content_sha256, preview_digest: 'visual-digest', reason: 'Retain this reviewed source', costs_confirmed: true, retained_visual_source_reviewed: true });
  expect(screen.getByRole('link', { name: /Dauerhaft aufbewahrten Auszug/ })).toHaveAttribute('href', '/belege/document');
});
it('retains an unknown exact visual command through a missing current review and withdrawn publication', async () => {
  const posts: { url: string; options: RequestInit }[] = []; let committed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
    if (options.method === 'POST') { posts.push({ url, options }); committed = true; return json({}, 503); }
    if (committed && url.includes('/disclosures')) return json({ items: [], next_cursor: null });
    return response(url, committed);
  }));
  const view = render(<AllocationAdoptionPanel {...props} />); await fill();
  fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Auszug aufbewahren' }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  expect(screen.getByRole('radio', { name: 'Vollständiges Original' })).toBeDisabled();
  view.rerender(<AllocationAdoptionPanel {...props} reviewId={null} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Übernahme mit Auszug erneut senden' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Übernahme mit Auszug erneut senden' }));
  await waitFor(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0]);
});
it('clears preview and acknowledgments on refresh and never restores a denied pending request', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => options.method === 'POST' ? json({}, 503) : deny ? json({}, 404) : response(url)));
  const view = render(<AllocationAdoptionPanel {...props} />); await fill();
  view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());
  await fill(); fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Auszug aufbewahren' }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  deny = true; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={2} />);
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Dieselbe Übernahme mit Auszug erneut senden' })).not.toBeInTheDocument());
  deny = false; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={3} />);
  await screen.findByRole('option', { name: /Seiten 1–2/ });
  expect(screen.queryByRole('button', { name: 'Dieselbe Übernahme mit Auszug erneut senden' })).not.toBeInTheDocument();
});
it('discards a late failed POST after a concurrent authorization denial without resurrecting its retry', async () => {
  let deny = false; let finish!: (value: Response) => void;
  const delayed = new Promise<Response>(resolve => { finish = resolve; });
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => options.method === 'POST' ? delayed : deny ? json({}, 404) : response(url)));
  const view = render(<AllocationAdoptionPanel {...props} />); await fill();
  fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Auszug aufbewahren' }));
  deny = true; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={1} />);
  await screen.findByText(/Auszüge derzeit nicht zugänglich/);
  finish(json({}, 503));
  await waitFor(() => expect(screen.queryByText(/Keine bestätigte Antwort/)).not.toBeInTheDocument());
  deny = false; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={2} />);
  await screen.findByRole('option', { name: /Seiten 1–2/ });
  expect(screen.queryByRole('button', { name: 'Dieselbe Übernahme mit Auszug erneut senden' })).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: 'Geprüfter Auszug' })).toBeEnabled();
});
