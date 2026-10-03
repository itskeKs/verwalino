import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ContextMappingPage } from './ContextMappingPage';
import type { ContextMapping } from '../api/contextMappings';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const first: ContextMapping = { id: 'v1', property_id: 'p', scope_id: 's', identifier_kind: 'meter_number', identifier_value: 'M-123', revision_number: 1, state: 'active', valid_from: '2026-01-01', valid_to: '2027-12-31', policy: 'manual-context-reuse-v1' };
function open() { render(<MemoryRouter initialEntries={['/zuordnungen/v1']}><Routes><Route path="/zuordnungen/:versionId" element={<ContextMappingPage />} /></Routes></MemoryRouter>); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('keeps unknown revocation pinned after a newer activation and distinguishes receipt from current state', async () => {
  let current = first; const requests: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push({ url, init }); return requests.length === 1 ? json({}, 503) : json({ ...first, id: 'v2', revision_number: 2, state: 'revoked' }, 201); }
    return json({ receipt: first, current, master_current: true, can_prepare: true });
  }));
  open(); fireEvent.change(await screen.findByLabelText('Begründung des Widerrufs'), { target: { value: 'Stop future reuse' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung widerrufen' }));
  await screen.findByText(/Keine bestätigte Antwort/); current = { ...first, id: 'v3', revision_number: 3 };
  fireEvent.click(screen.getByRole('button', { name: 'Zuordnung neu laden' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Widerruf erneut senden' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Widerruf erneut senden' }));
  await screen.findByText(/Widerruf bestätigt, Version 2/); await screen.findByText('Aktueller Zustand: aktiv');
  expect(requests[0].url).toBe(requests[1].url); expect(requests[0].init).toEqual(requests[1].init);
});
it('clears private revocation draft and receipt when access is denied', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : denied ? json({}, 404) : json({ receipt: first, current: first, master_current: false, can_prepare: true })));
  open(); fireEvent.change(await screen.findByLabelText('Begründung des Widerrufs'), { target: { value: 'Private draft' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung widerrufen' })); await screen.findByText(/Keine bestätigte Antwort/);
  denied = true; fireEvent.click(screen.getByRole('button', { name: 'Zuordnung neu laden' }));
  await screen.findByText(/derzeit nicht verfügbar/);
  expect(screen.queryByDisplayValue('Private draft')).not.toBeInTheDocument(); expect(screen.queryByText(/M-123/)).not.toBeInTheDocument();
});
