import { useEffect, useState, type FormEvent } from 'react';
import { getAiSettings, listAiModels, saveAiSettings } from '../api/endpoints';
import type { AiModel, OrganizationAiSettings, OrganizationAiSettingsBody } from '../api/types';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';

export function OrganizationAiSettingsSection({ organizationId }: { organizationId: string }) {
  const [settings, setSettings] = useState<OrganizationAiSettings | null>(null);
  const [models, setModels] = useState<AiModel[]>([]);
  const [key, setKey] = useState('');
  const [removeKey, setRemoveKey] = useState(false);
  const [limit, setLimit] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let current = true;
    getAiSettings(organizationId).then(value => {
      if (current) { setSettings(value); setLimit(value.monthly_limit_usd ?? ''); setError(null); }
    }).catch(() => { if (current) setError('Die KI-Einstellungen konnten nicht geladen werden.'); });
    return () => { current = false; };
  }, [organizationId, reload]);
  async function loadModels() {
    setBusy(true); setError(null); setMessage('');
    try {
      const result = await listAiModels(organizationId, key.trim() || undefined, settings?.provider, settings?.base_url);
      setModels(result.models);
      if (!result.models.length) setMessage('Der Anbieter hat für diesen Schlüssel keine Modelle zurückgegeben.');
    } catch (error) { setError(error instanceof Error ? error.message : 'Die Modellliste konnte nicht geladen werden.'); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!settings) return;
    setBusy(true); setError(null); setMessage('');
    const body: OrganizationAiSettingsBody = {
      provider:settings.provider, base_url:settings.base_url ?? null, model:settings.model, import_enabled:settings.import_enabled,
      qa_enabled:settings.qa_enabled, monthly_limit_usd:limit.trim() || null,
      ...(removeKey ? { api_key:null } : key.trim() ? { api_key:key.trim() } : {}),
    };
    try {
      const result = await saveAiSettings(organizationId, body);
      setSettings(result); setKey(''); setRemoveKey(false); setLimit(result.monthly_limit_usd ?? '');
      setMessage('KI-Einstellungen gespeichert. Sie gelten ab dem nächsten Auftrag oder der nächsten Frage.');
    } catch (error) { setError(error instanceof Error ? error.message : 'Die KI-Einstellungen konnten nicht gespeichert werden.'); }
    finally { setBusy(false); }
  }
  const selected = models.find(item => item.id === settings?.model);
  const known = selected?.price_known ?? settings?.price_known;
  return <section className="card mt-6 p-5 sm:p-6 min-w-0" aria-labelledby="ai-heading">
    <h2 id="ai-heading">KI für Ihre Organisation</h2>
    <p className="text-sm text-muted-foreground mt-2">Ihre Organisation verwendet ihren eigenen API-Schlüssel beim gewählten Anbieter. Das gewählte Modell gilt für alle Imports, Nachprüfungen und Fragen zum Objektwissen.</p>
    <p className="text-sm text-muted-foreground mt-2">Bei aktivierten Funktionen werden die benötigten Dokumentinhalte an den gewählten Anbieter bzw. die konfigurierte Basis-URL übermittelt. Verwalino stellt keinen API-Schlüssel bereit.</p>
    {error && <p role="alert" className="error-text mt-4">{error}</p>}
    {!settings ? error ? <Button variant="outline" onClick={() => setReload(value => value + 1)}>Erneut laden</Button> : <p role="status" className="mt-4">KI-Einstellungen werden geladen …</p> :
      <form onSubmit={event => void submit(event)} className="mt-5">
        <fieldset disabled={busy} className="space-y-5 min-w-0">
          <div><Label htmlFor="ai-provider">Anbieter</Label>
            <select id="ai-provider" className="w-full min-w-0 rounded-md border border-input bg-background p-2 text-sm" value={settings.provider} onChange={event => {
              setSettings({...settings, provider:event.target.value as OrganizationAiSettings['provider'], base_url:null, model:null, key_set:false, price_known:false, import_enabled:false, qa_enabled:false});
              setKey(''); setModels([]); setRemoveKey(false); setMessage('Bitte geben Sie für den neuen Anbieter einen eigenen Schlüssel ein und laden Sie dessen Modelle.');
            }}>
              <option value="openai">OpenAI</option><option value="openai_compatible">OpenAI-kompatible API</option><option value="anthropic">Anthropic</option>
            </select>
          </div>
          {settings.provider === 'openai_compatible' && <div>
            <Label htmlFor="ai-base-url">API-Basis-URL</Label>
            <Input id="ai-base-url" type="url" value={settings.base_url ?? ''} maxLength={2048} placeholder="http://localhost:11434/v1" onChange={event => {
              setSettings({...settings, base_url:event.target.value, key_set:false, model:null, price_known:false, import_enabled:false, qa_enabled:false}); setKey(''); setModels([]); setMessage('');
            }} aria-describedby="ai-endpoint-info" />
            <p id="ai-endpoint-info" className="text-sm text-muted-foreground mt-2">Verwenden Sie die API-Adresse einschließlich Pfad, z. B. /v1. Lokale Ziele sind nur im selbst gehosteten Betrieb verfügbar. Auch ein lokaler Dienst benötigt hier einen Schlüsselwert. Prüfen Sie Datenschutz und Speicherregeln des Anbieters.</p>
          </div>}
          {settings.provider === 'anthropic' && <p className="text-sm text-muted-foreground">Verwenden Sie einen Anthropic-Schlüssel für einen einzelnen Workspace. Prüfen Sie die Datenaufbewahrung Ihres Vertrags; Verwalino aktiviert keine Zero Data Retention.</p>}
          <div>
            <Label htmlFor="ai-key">API-Schlüssel</Label>
            <Input id="ai-key" type="password" autoComplete="off" spellCheck={false} value={key} maxLength={4096} onChange={event => { setKey(event.target.value); setRemoveKey(false); setMessage(''); }} aria-describedby="ai-key-status" />
            <p id="ai-key-status" className="text-sm text-muted-foreground mt-2">{removeKey ? 'Der gespeicherte Schlüssel wird beim Speichern entfernt.' : settings.key_set ? 'Ein Schlüssel ist gespeichert. Leer lassen, um ihn beizubehalten; zum Ersetzen einen neuen eingeben.' : 'Es ist noch kein Schlüssel gespeichert.'} Der gespeicherte Schlüssel wird niemals angezeigt.</p>
            {settings.key_set && <label className="flex gap-2 items-start mt-3 text-sm"><input type="checkbox" checked={removeKey} onChange={event => {
              setRemoveKey(event.target.checked); setKey(''); setMessage('');
              if (event.target.checked) setSettings({...settings, import_enabled:false, qa_enabled:false});
            }} />Gespeicherten Schlüssel entfernen und KI-Funktionen ausschalten</label>}
          </div>
          <div><Button type="button" variant="outline" disabled={removeKey || (!key.trim() && !settings.key_set)} onClick={() => void loadModels()}>{busy ? 'Bitte warten …' : 'Modelle laden'}</Button></div>
          <div>
            <Label htmlFor="ai-model">Modell</Label>
            <select id="ai-model" className="w-full min-w-0 rounded-md border border-input bg-background p-2 text-sm" value={settings.model ?? ''} onChange={event => {
              const choice = models.find(item => item.id === event.target.value);
              setSettings({...settings, model:event.target.value || null, price_known:choice?.price_known ?? false}); setMessage('');
            }}>
              <option value="">Bitte Modelle laden und auswählen</option>
              {settings.model && !models.some(item => item.id === settings.model) && <option value={settings.model}>{settings.model}</option>}
              {models.map(model => <option key={model.id} value={model.id}>{model.id}{model.evaluated ? ' · geprüft' : ''}</option>)}
            </select>
            <p className="text-sm text-muted-foreground mt-2">Alle vom Anbieter gelisteten Modelle sind auswählbar. „geprüft“ kennzeichnet bestandene Verwalino-Evaluationen. Andere Modelle können erforderliche Funktionen ablehnen.</p>
          </div>
          <label className="flex gap-2 items-start"><input type="checkbox" checked={settings.import_enabled} disabled={removeKey} onChange={event => { setSettings({...settings, import_enabled:event.target.checked}); setMessage(''); }} />Teilungserklärungen importieren</label>
          <label className="flex gap-2 items-start"><input type="checkbox" checked={settings.qa_enabled} disabled={removeKey} onChange={event => { setSettings({...settings, qa_enabled:event.target.checked}); setMessage(''); }} />Fragen zum Objektwissen</label>
          <div>
            <Label htmlFor="ai-monthly-limit">Monatliches Limit in USD (optional)</Label>
            <Input id="ai-monthly-limit" type="number" min="0" step="0.01" inputMode="decimal" value={limit} onChange={event => { setLimit(event.target.value); setMessage(''); }} aria-describedby="ai-budget-info" />
            <p id="ai-budget-info" className="text-sm text-muted-foreground mt-2">Leer lassen für kein monatliches Limit. Das Limit gilt pro Kalendermonat (UTC) für Imports und Fragen zusammen, einschließlich laufender Reservierungen. Die Abrechnung erfolgt durch den gewählten Anbieter.</p>
            {settings.model && !known && <p className="text-sm mt-2">Für dieses Modell ist kein Preis bekannt. Das monatliche Limit wird für dieses Modell nicht durchgesetzt.</p>}
          </div>
          <Button type="submit" disabled={busy}>{busy ? 'Wird gespeichert …' : 'KI-Einstellungen speichern'}</Button>
        </fieldset>
        <p role="status" className="text-sm mt-3">{message}</p>
      </form>}
  </section>;
}
