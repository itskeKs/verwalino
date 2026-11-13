import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { LegalAssentsSection } from './LegalAssentsSection';
import { api } from '../api/client';

it('lists both assents with version, time and administrator', async () => {
  vi.spyOn(api, 'get').mockResolvedValue([
    { document: 'TERMS', version: '2026-11-13', assented_at: '2026-11-13T10:00:00+00:00', admin_name: 'Sabine Beispiel' },
    { document: 'AV', version: '2026-11-13', assented_at: '2026-11-13T10:00:00+00:00', admin_name: 'Sabine Beispiel' },
  ] as never);
  render(<LegalAssentsSection />);
  await screen.findByText(/Nutzungsbedingungen/);
  expect(screen.getByText(/Auftragsverarbeitungsvertrag/)).toBeVisible();
  expect(screen.getAllByText(/Version 2026-11-13/).length).toBe(2);
  expect(screen.getAllByText(/durch Sabine Beispiel/).length).toBe(2);
});
