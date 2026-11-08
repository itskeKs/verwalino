import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { api } from '../api/client';

const auth = vi.hoisted(() => ({ session: { user_id:'admin', organization_id:'org-a', role:'OWNER_ADMIN' } }));
vi.mock('../auth/useAuth', () => ({ useAuth: () => auth }));
vi.mock('../design/useDesignTheme', () => ({ useDesignTheme: () => ({ theme:'klar', loaded:true, loading:false, saving:false, error:null, save:vi.fn(), reload:vi.fn() }) }));
const initial = { provider:'openai', key_set:false, model:null, import_enabled:false, qa_enabled:false, monthly_limit_usd:null, price_known:false };
beforeEach(() => {
  vi.restoreAllMocks(); auth.session.role = 'OWNER_ADMIN';
  vi.spyOn(api, 'get').mockResolvedValue(initial);
});

it('allows every listed model, explains unknown prices and clears a saved key', async () => {
  vi.spyOn(api,'post').mockResolvedValue({models:[{id:'future-model-with-a-long-name-2026-11-07', evaluated:false, price_known:false},{id:'evaluated-model', evaluated:true, price_known:true}]});
  const save = vi.spyOn(api,'put').mockImplementation(async (_url, body) => ({...initial, ...body as object, key_set:true}));
  render(<SettingsPage />);
  const key = await screen.findByLabelText('OpenAI-API-Schlüssel');
  fireEvent.change(key,{target:{value:'synthetic-entered-key'}});
  fireEvent.click(screen.getByRole('button',{name:'Modelle laden'}));
  await screen.findByRole('option',{name:'evaluated-model · geprüft'});
  fireEvent.change(screen.getByLabelText('Modell'),{target:{value:'future-model-with-a-long-name-2026-11-07'}});
  expect(screen.getByText(/Für dieses Modell ist kein Preis bekannt/)).toBeVisible();
  fireEvent.click(screen.getByLabelText('Teilungserklärungen importieren'));
  fireEvent.click(screen.getByLabelText('Fragen zum Objektwissen'));
  fireEvent.change(screen.getByLabelText('Monatliches Limit in USD (optional)'),{target:{value:'12.50'}});
  fireEvent.click(screen.getByRole('button',{name:'KI-Einstellungen speichern'}));
  await screen.findByText('KI-Einstellungen gespeichert. Sie gelten ab dem nächsten Auftrag oder der nächsten Frage.');
  expect(key).toHaveValue('');
  expect(save).toHaveBeenCalledWith('/api/organizations/org-a/ai-settings',expect.objectContaining({api_key:'synthetic-entered-key', import_enabled:true,qa_enabled:true,monthly_limit_usd:'12.50'}));
});

it('retains a typed key and chosen model after a failed save', async () => {
  vi.spyOn(api,'get').mockResolvedValue({...initial, key_set:true,model:'gpt-5.6-luna',price_known:true});
  vi.spyOn(api,'put').mockRejectedValue(new Error('Speichern fehlgeschlagen.'));
  render(<SettingsPage />);
  const key = await screen.findByLabelText('OpenAI-API-Schlüssel');
  fireEvent.change(key,{target:{value:'synthetic-replacement'}});
  fireEvent.click(screen.getByRole('button',{name:'KI-Einstellungen speichern'}));
  await screen.findByRole('alert');
  expect(key).toHaveValue('synthetic-replacement');
  expect(screen.getByLabelText('Modell')).toHaveValue('gpt-5.6-luna');
});

it('keeps personal settings for non-admins without fetching organization AI settings', async () => {
  auth.session.role = 'READ_ONLY';
  render(<SettingsPage />);
  expect(screen.getByRole('heading',{name:'Persönliches Design'})).toBeVisible();
  expect(screen.queryByLabelText('OpenAI-API-Schlüssel')).not.toBeInTheDocument();
  await waitFor(() => expect(api.get).not.toHaveBeenCalled());
});
