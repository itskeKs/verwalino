import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { WegSettlementDetailPage } from './WegSettlementDetailPage';
import { AssetReportDetailPage } from './AssetReportDetailPage';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const run = { id: 'draft', property_id: 'property', run_type: 'WEG_JAHRESABRECHNUNG', status: 'draft', economic_plan_id: 'plan', period_start: '2026-01-01', period_end: '2026-12-31', lines: [], superseded_by_id: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('keeps one stable plan selector when its parent blocks financial actions', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/economic-plan-successor')) return json({ run_id: 'draft', current_plan_id: 'plan', successor_id: 'new-plan', plan_year: 2026 });
    if (url.includes('/api/weg-settlements/')) return json(run);
    if (url.includes('/api/economic-plans/')) return json({ lines: [] });
    return json([]);
  }));
  render(<MemoryRouter initialEntries={['/weg/draft']}><Routes><Route path="/weg/:runId" element={<WegSettlementDetailPage />} /></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Nachfolger des Wirtschaftsplans prüfen' }));
  await screen.findByLabelText('Grundlage der Auswahl');
  expect(screen.getAllByRole('region', { name: 'Korrigierten Wirtschaftsplan auswählen' })).toHaveLength(1);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Berechnen' })).toBeEnabled());
});

it.each(['weg', 'asset'])('clears manual inputs when navigating to a different %s draft', async family => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/api/weg-settlements/')) return json({ ...run, id: url.split('/').at(-1), economic_plan_id: family === 'weg' ? 'plan' : null, run_type: family === 'weg' ? 'WEG_JAHRESABRECHNUNG' : 'VERMOEGENSBERICHT' });
    if (url.includes('/api/economic-plans/')) return json({ lines: [] });
    return json([]);
  }));
  render(<MemoryRouter initialEntries={['/run/first']}><Link to="/run/second">Next draft</Link><Routes><Route path="/run/:runId" element={family === 'weg' ? <WegSettlementDetailPage /> : <AssetReportDetailPage />} /></Routes></MemoryRouter>);
  const label = family === 'weg' ? 'Rücklagen-Anfangsbestand' : 'Rücklage Anfangsbestand';
  fireEvent.change(await screen.findByLabelText(label), { target: { value: '125.00' } });
  fireEvent.click(screen.getByRole('link', { name: 'Next draft' }));
  await waitFor(() => expect(screen.getByLabelText(label)).toHaveValue(''));
});

it.each(['weg', 'asset'])('ignores a late financial command from the previous %s page', async family => {
  let finish: ((value: Response) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') return new Promise<Response>(resolve => { finish = resolve; });
    if (url.includes('/api/weg-settlements/')) return json({ ...run, id: url.split('/').at(-1), economic_plan_id: family === 'weg' ? 'plan' : null });
    if (url.includes('/api/economic-plans/')) return json({ lines: [] });
    return json([]);
  }));
  render(<MemoryRouter initialEntries={['/run/first']}><Link to="/run/second">Next draft</Link><Routes><Route path="/run/:runId" element={family === 'weg' ? <WegSettlementDetailPage /> : <AssetReportDetailPage />} /></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Berechnen' }));
  fireEvent.click(screen.getByRole('link', { name: 'Next draft' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Berechnen' })).toBeEnabled());
  await act(async () => finish!(json({ ...run, id: 'first', status: 'finalized', snapshot_sha256: 'old-command-hash' })));
  expect(screen.queryByText('old-command-hash')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Berechnen' })).toBeEnabled();
});
