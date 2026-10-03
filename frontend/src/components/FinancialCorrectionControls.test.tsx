import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { FinancialCorrectionControls, PlanSuccessorSelector } from './FinancialCorrectionControls';
import type { WegRun } from '../api/types';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const run = { id: 'original', run_type: 'WEG_JAHRESABRECHNUNG', status: 'finalized', superseded_by_id: null } as WegRun;
const candidate = { run_id: 'draft', current_plan_id: 'old-plan', successor_id: 'new-plan', plan_year: 2026, period_start: '2026-01-01', period_end: '2026-12-31', monthly_due_day: null };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['WEG_JAHRESABRECHNUNG', 'VERMOEGENSBERICHT'])('requires deliberate correction creation and links the right financial family (%s)', async family => {
  const fetch = vi.fn(async () => json({ id: 'successor' }, 201)); vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><FinancialCorrectionControls run={{ ...run, run_type: family }} /></MemoryRouter>);
  const create = screen.getByRole('button', { name: 'Korrekturentwurf anlegen' }); expect(create).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(create);
  expect(await screen.findByRole('link', { name: 'Korrekturvorgang öffnen' })).toHaveAttribute('href', `${family === 'VERMOEGENSBERICHT' ? '/vermoegensberichte' : '/weg-abrechnungen'}/successor`);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('recovers a committed correction after lost reply without creating another draft', async () => {
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => { calls.push(url); return url.endsWith('/correction') ? json({}, 503) : json({ ...run, superseded_by_id: 'recovered' }); }));
  render(<MemoryRouter><FinancialCorrectionControls run={run} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Korrekturentwurf anlegen' }));
  await screen.findByRole('alert'); expect(screen.queryByRole('button', { name: 'Korrekturentwurf anlegen' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Ausgang der Korrektur prüfen' }));
  expect(await screen.findByRole('link')).toHaveAttribute('href', '/weg-abrechnungen/recovered');
  expect(calls).toEqual(['/api/settlements/original/correction', '/api/weg-settlements/original']);
});

it('requires a fresh acknowledgment when recovery finds no committed correction', async () => {
  let count = 0; vi.stubGlobal('fetch', vi.fn(async () => count++ === 0 ? json({}, 503) : json(run)));
  render(<MemoryRouter><FinancialCorrectionControls run={run} /></MemoryRouter>);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Korrekturentwurf anlegen' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Ausgang der Korrektur prüfen' }));
  expect(await screen.findByRole('button', { name: 'Korrekturentwurf anlegen' })).toBeDisabled();
});

it('retains the exact reviewed plan choice through a lost reply', async () => {
  const bodies: string[] = []; const applied = vi.fn(); const blocked = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method !== 'PATCH') return json(candidate);
    bodies.push(options.body as string); return bodies.length === 1 ? json({}, 503) : json({ id: 'draft' });
  }));
  render(<PlanSuccessorSelector runId="draft" onApplied={applied} onBlockedChange={blocked} />);
  fireEvent.click(screen.getByRole('button', { name: 'Nachfolger des Wirtschaftsplans prüfen' }));
  const apply = await screen.findByRole('button', { name: 'Geprüften Wirtschaftsplan übernehmen' }); expect(apply).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed revised obligations' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Reviewed complete corrected plan' } }); expect(apply).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(apply);
  await screen.findByRole('alert'); expect(applied).not.toHaveBeenCalled(); expect(screen.getByRole('textbox')).toBeDisabled();
  expect(blocked).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Planauswahl erneut senden' }));
  await waitFor(() => expect(applied).toHaveBeenCalledOnce());
  expect(blocked).toHaveBeenLastCalledWith(false);
  expect(bodies).toHaveLength(2); expect(bodies[0]).toBe(bodies[1]);
});

it('disables plan actions while another financial command is active', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  render(<PlanSuccessorSelector runId="draft" disabled onApplied={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Nachfolger des Wirtschaftsplans prüfen' })).toBeDisabled();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([403, 409])('clears candidate and requires new review after selection refusal %s', async code => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'PATCH' ? json({}, code) : json(candidate)));
  render(<PlanSuccessorSelector runId="draft" onApplied={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Nachfolger des Wirtschaftsplans prüfen' }));
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: 'Reviewed' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Geprüften Wirtschaftsplan übernehmen' }));
  await screen.findByRole('alert'); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Dieselbe Planauswahl erneut senden' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Nachfolger des Wirtschaftsplans prüfen' })).toBeEnabled();
});
