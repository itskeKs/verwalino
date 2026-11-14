import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { api } from '../api/client';

const auth = vi.hoisted(() => ({ session: { user_id:'admin', organization_id:'org-a', role:'OWNER_ADMIN' } }));
vi.mock('../auth/useAuth', () => ({ useAuth: () => auth }));
vi.mock('../design/useDesignTheme', () => ({ useDesignTheme: () => ({ theme:'klar', loaded:true, loading:false, saving:false, error:null, save:vi.fn(), reload:vi.fn() }) }));
const initial = { provider:'openai', base_url:null, key_set:false, model:null, import_enabled:false, qa_enabled:false, monthly_limit_usd:null, price_known:false };
const byUrl = (value: object) => vi.spyOn(api, 'get').mockImplementation(async (url: string) => (url.includes('/legal-assents') ? [] : value) as never);
beforeEach(() => {
  vi.restoreAllMocks(); auth.session.role = 'OWNER_ADMIN';
  byUrl(initial);
});

it('allows every listed model, explains unknown prices and clears a saved key', async () => {
  vi.spyOn(api,'post').mockResolvedValue({models:[{id:'future-model-with-a-long-name-2026-11-07', evaluated:false, price_known:false},{id:'evaluated-model', evaluated:true, price_known:true}]});
  const save = vi.spyOn(api,'put').mockImplementation(async (_url, body) => ({...initial, ...body as object, key_set:true}));
  render(<SettingsPage />);
  const key = await screen.findByLabelText('API-Schlüssel');
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
  byUrl({...initial, key_set:true,model:'gpt-5.6-luna',price_known:true});
  vi.spyOn(api,'put').mockRejectedValue(new Error('Speichern fehlgeschlagen.'));
  render(<SettingsPage />);
  const key = await screen.findByLabelText('API-Schlüssel');
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
  expect(screen.queryByLabelText('API-Schlüssel')).not.toBeInTheDocument();
  await waitFor(() => expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/ai-settings')));
});


it('switches provider and endpoint without carrying the old key or model to discovery', async () => {
  byUrl({...initial, key_set:true, model:'gpt-5.6-luna'});
  const discover = vi.spyOn(api,'post').mockResolvedValue({models:[{id:'local-very-long-model-name',evaluated:false,price_known:false}]});
  render(<SettingsPage />);
  fireEvent.change(await screen.findByLabelText('Anbieter'), {target:{value:'openai_compatible'}});
  expect(screen.getByLabelText('Modell')).toHaveValue('');
  expect(screen.getByRole('button',{name:'Modelle laden'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('API-Basis-URL'), {target:{value:'http://localhost:11434/v1'}});
  fireEvent.change(screen.getByLabelText('API-Schlüssel'), {target:{value:'synthetic-local-placeholder'}});
  fireEvent.click(screen.getByRole('button',{name:'Modelle laden'}));
  await screen.findByRole('option',{name:'local-very-long-model-name'});
  expect(discover).toHaveBeenCalledWith('/api/organizations/org-a/ai-settings/models', {provider:'openai_compatible',base_url:'http://localhost:11434/v1',api_key:'synthetic-local-placeholder'});
});


it('saves an endpoint timeout and resets it when the destination changes', async () => {
  byUrl({...initial,provider:'openai_compatible',base_url:'http://localhost:11434/v1',request_timeout_seconds:240});
  const save = vi.spyOn(api,'put').mockImplementation(async (_url,body) => ({...initial,...body as object}));
  render(<SettingsPage />);
  const timeout = await screen.findByLabelText('Zeitlimit pro Anfrage in Sekunden (optional)');
  expect(timeout).toHaveValue(240);
  fireEvent.change(timeout,{target:{value:'300'}});
  fireEvent.click(screen.getByRole('button',{name:'KI-Einstellungen speichern'}));
  await screen.findByText('KI-Einstellungen gespeichert. Sie gelten ab dem nächsten Auftrag oder der nächsten Frage.');
  expect(save).toHaveBeenCalledWith('/api/organizations/org-a/ai-settings',expect.objectContaining({request_timeout_seconds:300}));
  fireEvent.change(screen.getByLabelText('API-Basis-URL'),{target:{value:'http://localhost:1234/v1'}});
  expect(timeout).toHaveValue(null);
  expect(timeout).toHaveAttribute('placeholder','180');
});

it('words the recovery hint in German and gives the second-factor card the gap of its neighbours', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (url: string) => (url.includes('/legal-assents') ? [] : url === '/api/auth/second-factor' ? { enrolled: true } : initial) as never);
  render(<SettingsPage />);
  const card = (await screen.findByRole('heading', { name: 'Zweiter Faktor' })).closest('section')!;
  expect(card).toHaveTextContent('Ohne Telefon melden Sie sich mit einem der zehn Wiederherstellungscodes an.');
  expect(card).not.toHaveTextContent('signieren');
  // Same vertical gap and text size as the AI settings card above it.
  const neighbour = screen.getByRole('heading', { name: 'KI für Ihre Organisation' }).closest('section')!;
  for (const name of ['mt-6', 'p-5', 'text-sm']) expect(card).toHaveClass(name);
  expect(neighbour).toHaveClass('mt-6');
});
