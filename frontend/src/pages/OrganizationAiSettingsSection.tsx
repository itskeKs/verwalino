import { useEffect, useState, type FormEvent } from 'react';
import { getAiSettings, listAiModels, saveAiSettings } from '../api/endpoints';
import { ApiError } from '../api/client';
import type { AiModel, OrganizationAiSettings, OrganizationAiSettingsBody } from '../api/types';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { formatDateTime } from '../lib/format';

// "a, b und c": the kinds of document a text names, in German.
const list = (parts: string[]) => parts.length < 2 ? parts.join('') : `${parts.slice(0, -1).join(', ')} und ${parts[parts.length - 1]}`;

// One selector for both model roles, fed from the same provider model list.
function ModelSelect({ id, label, value, models, hint, onChange }: {
  id: string; label: string; value: string | null | undefined; models: AiModel[]; hint: string; onChange: (model: AiModel | undefined, id: string | null) => void;
}) {
  return <div>
    <Label htmlFor={id}>{label}</Label>
    <select id={id} className="w-full min-w-0 rounded-md border border-input bg-background p-2 text-sm" value={value ?? ''} aria-describedby={`${id}-hint`} onChange={event => {
      onChange(models.find(item => item.id === event.target.value), event.target.value || null);
    }}>
      <option value="">Bitte Modelle laden und auswählen</option>
      {value && !models.some(item => item.id === value) && <option value={value}>{value}</option>}
      {models.map(model => <option key={model.id} value={model.id}>{model.id}{model.evaluated ? ' · geprüft' : ''}</option>)}
    </select>
    <p id={`${id}-hint`} className="text-sm text-muted-foreground mt-2">{hint}</p>
  </div>;
}

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
  const [confirmation, setConfirmation] = useState(false);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
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
      provider:settings.provider, base_url:settings.base_url ?? null, model:settings.model, document_model:settings.document_model ?? null, import_enabled:settings.import_enabled,
      qa_enabled:settings.qa_enabled, monthly_limit_usd:limit.trim() || null, request_timeout_seconds:settings.request_timeout_seconds ?? null,
      ...(removeKey ? { api_key:null } : key.trim() ? { api_key:key.trim() } : {}),
      ...(confirmationNeeded ? { confirmation } : {}),
    };
    try {
      const result = await saveAiSettings(organizationId, body);
      setSettings(result); setKey(''); setRemoveKey(false); setConfirmation(false); setConfirmationError(null); setLimit(result.monthly_limit_usd ?? '');
      setMessage('KI-Einstellungen gespeichert. Sie gelten ab dem nächsten Auftrag oder der nächsten Frage.');
    } catch (error) {
      if (error instanceof ApiError && error.code === 'ORGANIZATION_AI_CONFIRMATION_REQUIRED') setConfirmationError(error.message);
      else setError(error instanceof Error ? error.message : 'Die KI-Einstellungen konnten nicht gespeichert werden.');
    }
    finally { setBusy(false); }
  }
  const known = models.find(item => item.id === settings?.model)?.price_known ?? settings?.price_known;
  const documentKnown = models.find(item => item.id === settings?.document_model)?.price_known ?? settings?.document_price_known;
  // Storing or replacing the key, or confirming a stored key that predates
  // the confirmation, requires the provider confirmation in the same save.
  // An older confirmation keeps what its text covered and asks again for the rest: one given before invoices were covered
  // keeps the declaration import, one given before the inbox was covered keeps the declaration import and the invoices.
  const stillRequired = Boolean(settings?.invoice_confirmation_required || settings?.inbox_confirmation_required);
  const confirmationNeeded = !removeKey && Boolean(key.trim() || (settings?.key_set && (settings.confirmation_required || stillRequired)));
  const earlierConfirmation = Boolean(settings?.key_set && !settings.confirmation_required && stillRequired && settings.confirmation && !removeKey && !key.trim());
  // Invoices and the documents of the inbox are named only where this installation sends them (the settings answer, from
  // the one table of confirmation texts); until the settings are loaded nothing is claimed.
  const invoices = settings?.invoice_processing_enabled === true;
  const inbox = settings?.inbox_processing_enabled === true;
  const sent = [...(invoices ? ['Rechnungen'] : []), ...(inbox ? ['Dokumente im Posteingang'] : [])];
  const documents = sent.length ? `Dokumente (${list(['Teilungserklärungen', ...sent])})` : 'Dokumente';
  // What a stored confirmation of an earlier text does not name yet, and what it still covers.
  const missing = [...(settings?.invoice_confirmation_required ? ['Rechnungen'] : []), ...(settings?.inbox_confirmation_required ? ['Dokumente im Posteingang'] : [])];
  const covered = ['Teilungserklärungen', ...(invoices && !settings?.invoice_confirmation_required ? ['Rechnungen'] : []), ...(inbox && !settings?.inbox_confirmation_required ? ['Dokumente im Posteingang'] : [])];
  return <section className="card mt-6 p-5 sm:p-6 min-w-0" aria-labelledby="ai-heading">
    <h2 id="ai-heading">KI für Ihre Organisation</h2>
    <p className="text-sm text-muted-foreground mt-2">Ihre Organisation verwendet ihren eigenen API-Schlüssel beim gewählten Anbieter. Sie wählen zwei Modelle des Anbieters: eines zum Einlesen Ihrer Dokumente und eines zum Extrahieren der Informationen. Dasselbe Modell darf für beide Aufgaben gewählt werden.</p>
    <p className="text-sm text-muted-foreground mt-2">Bei aktivierten Funktionen werden die benötigten Dokumentinhalte an den gewählten Anbieter bzw. die konfigurierte Basis-URL übermittelt.{sent.length > 0 && ` Das gilt auch für ${sent.join(' und für ')}: Hochgeladene ${invoices && inbox ? 'Rechnungen und Dokumente' : invoices ? 'Rechnungen' : 'Dokumente'} (PDF, PNG oder JPEG) ohne strukturierte E-Rechnungsdaten (zum Beispiel Scans und gewöhnliche PDF-Dateien) werden zum Auswerten an den Anbieter übermittelt. E-Rechnungen im Format XRechnung oder ZUGFeRD werden direkt gelesen und nicht übermittelt.`} Verwalino stellt keinen API-Schlüssel bereit.</p>
    {error && <p role="alert" className="error-text mt-4">{error}</p>}
    {!settings ? error ? <Button variant="outline" onClick={() => setReload(value => value + 1)}>Erneut laden</Button> : <p role="status" className="mt-4">KI-Einstellungen werden geladen …</p> :
      <form onSubmit={event => void submit(event)} className="mt-5">
        <fieldset disabled={busy} className="space-y-5 min-w-0">
          <div><Label htmlFor="ai-provider">Anbieter</Label>
            <select id="ai-provider" className="w-full min-w-0 rounded-md border border-input bg-background p-2 text-sm" value={settings.provider} onChange={event => {
              setSettings({...settings, provider:event.target.value as OrganizationAiSettings['provider'], base_url:null, request_timeout_seconds:null, model:null, document_model:null, key_set:false, price_known:false, document_price_known:false, import_enabled:false, qa_enabled:false});
              setKey(''); setModels([]); setRemoveKey(false); setConfirmation(false); setConfirmationError(null); setMessage('Bitte geben Sie für den neuen Anbieter einen eigenen Schlüssel ein und laden Sie dessen Modelle.');
            }}>
              <option value="openai">OpenAI</option><option value="openai_compatible">OpenAI-kompatible API</option><option value="anthropic">Anthropic</option>
            </select>
          </div>
          {settings.provider === 'openai_compatible' && <div>
            <Label htmlFor="ai-base-url">API-Basis-URL</Label>
            <Input id="ai-base-url" type="url" value={settings.base_url ?? ''} maxLength={2048} placeholder="http://localhost:11434/v1" onChange={event => {
              setSettings({...settings, base_url:event.target.value, request_timeout_seconds:null, key_set:false, model:null, document_model:null, price_known:false, document_price_known:false, import_enabled:false, qa_enabled:false}); setKey(''); setModels([]); setConfirmation(false); setConfirmationError(null); setMessage('');
            }} aria-describedby="ai-endpoint-info" />
            <p id="ai-endpoint-info" className="text-sm text-muted-foreground mt-2">Verwenden Sie die API-Adresse einschließlich Pfad, z. B. /v1. Lokale Ziele sind nur im selbst gehosteten Betrieb verfügbar. Auch ein lokaler Dienst benötigt hier einen Schlüsselwert. Prüfen Sie Datenschutz und Speicherregeln des Anbieters.</p>
          </div>}
          <div>
            <Label htmlFor="ai-timeout">Zeitlimit pro Anfrage in Sekunden (optional)</Label>
            <Input id="ai-timeout" type="number" min="1" max="3600" step="1" inputMode="numeric" value={settings.request_timeout_seconds ?? ''} placeholder={settings.provider === 'openai_compatible' ? '180' : undefined} onChange={event => {
              setSettings({...settings, request_timeout_seconds:event.target.value === '' ? null : Number(event.target.value)}); setMessage('');
            }} aria-describedby="ai-timeout-info" />
            <p id="ai-timeout-info" className="text-sm text-muted-foreground mt-2">Leer lassen für den Anbieterstandard: kompatible APIs 180 Sekunden, OpenAI und Anthropic die bestehenden Funktionsstandards. Möglich sind 1 bis 3600 Sekunden. Das Zeitlimit gilt ab der nächsten Anfrage und wird bei einem Wechsel von Anbieter oder API-Adresse zurückgesetzt.</p>
          </div>
          {settings.provider === 'anthropic'  && <p className="text-sm text-muted-foreground">Verwenden Sie einen Anthropic-Schlüssel für einen einzelnen Workspace. Prüfen Sie die Datenaufbewahrung Ihres Vertrags; Verwalino aktiviert keine Zero Data Retention.</p>}
          <div>
            <Label htmlFor="ai-key">API-Schlüssel</Label>
            <Input id="ai-key" type="password" autoComplete="off" spellCheck={false} value={key} maxLength={4096} onChange={event => { setKey(event.target.value); setRemoveKey(false); setConfirmationError(null); setMessage(''); }} aria-describedby="ai-key-status" />
            <p id="ai-key-status" className="text-sm text-muted-foreground mt-2">{removeKey ? 'Der gespeicherte Schlüssel wird beim Speichern entfernt.' : settings.key_set ? 'Ein Schlüssel ist gespeichert. Leer lassen, um ihn beizubehalten; zum Ersetzen einen neuen eingeben.' : 'Es ist noch kein Schlüssel gespeichert.'} Der gespeicherte Schlüssel wird niemals angezeigt.</p>
            {settings.key_set && <label className="flex gap-2 items-start mt-3 text-sm"><input type="checkbox" checked={removeKey} onChange={event => {
              setRemoveKey(event.target.checked); setKey(''); setConfirmation(false); setConfirmationError(null); setMessage('');
              if (event.target.checked) setSettings({...settings, import_enabled:false, qa_enabled:false});
            }} />Gespeicherten Schlüssel entfernen und KI-Funktionen ausschalten</label>}
            {settings.key_set && !settings.confirmation_required && !stillRequired && settings.confirmation && !removeKey && !key.trim() ?
              <p className="text-sm text-muted-foreground mt-3">Bestätigt am {formatDateTime(settings.confirmation.confirmed_at)} von {settings.confirmation.confirmed_by_name}.</p> :
              confirmationNeeded && <div className="mt-3">
                {earlierConfirmation && settings.confirmation && <p className="text-sm mt-3 mb-2">Die Bestätigung vom {formatDateTime(settings.confirmation.confirmed_at)} von {settings.confirmation.confirmed_by_name} galt, bevor {list(missing)} ausgewertet wurden. {list(covered)} sind davon nicht betroffen. {list(missing)} werden erst nach einer neuen Bestätigung an den Anbieter übermittelt; bis dahin bleiben sie in Verwalino und können von Hand erfasst werden. Den Schlüssel müssen Sie dafür nicht erneut eingeben.</p>}
                <label className="flex gap-2 items-start text-sm">
                  <input type="checkbox" checked={confirmation} onChange={event => { setConfirmation(event.target.checked); setConfirmationError(null); setMessage(''); }} />
                  <span>Wir haben den Anbieter selbst gewählt und mit ihm einen Vertrag geschlossen, der die Datenverarbeitung, den Speicherort und den Verzicht auf eine Trainingsnutzung abdeckt. Wir weisen den Betreiber dieser Installation an, {documents} und extrahierte Texte an den gewählten Anbieter zu übermitteln.</span>
                </label>
                {confirmationError && <p role="alert" className="error-text mt-2">{confirmationError}</p>}
              </div>}
          </div>
          <div><Button type="button" variant="outline" disabled={removeKey || (!key.trim() && !settings.key_set)} onClick={() => void loadModels()}>{busy ? 'Bitte warten …' : 'Modelle laden'}</Button></div>
          <ModelSelect id="ai-document-model" label="Dokumente einlesen" value={settings.document_model} models={models}
            hint="Dieses Modell liest die Seiten Ihrer Dokumente und überträgt, was dort zu sehen ist. Es muss Bilder lesen können."
            onChange={(choice, id) => { setSettings({...settings, document_model:id, document_price_known:choice?.price_known ?? false}); setMessage(''); }} />
          {settings.import_enabled && !settings.document_model && <p className="text-sm -mt-3">Für den Import von Teilungserklärungen fehlt noch ein Modell unter „Dokumente einlesen“. Ohne diese Auswahl startet kein Import; bereits hochgeladene Dokumente bleiben erhalten.{sent.length > 0 && ` ${list(sent)}, die gescannt sind oder Seiten ohne Text haben, werden dann ebenfalls nicht ausgewertet; ihre Originale bleiben erhalten.`}</p>}
          <ModelSelect id="ai-model" label="Informationen extrahieren" value={settings.model} models={models}
            hint="Dieses Modell wertet das Eingelesene aus, schlägt Zuordnungen vor und beantwortet Fragen zum Objektwissen. Alle vom Anbieter gelisteten Modelle sind auswählbar. „geprüft“ kennzeichnet bestandene Verwalino-Evaluationen. Andere Modelle können erforderliche Funktionen ablehnen."
            onChange={(choice, id) => { setSettings({...settings, model:id, price_known:choice?.price_known ?? false}); setMessage(''); }} />
          {sent.length > 0 ? <div>
            <label className="flex gap-2 items-start"><input type="checkbox" checked={settings.import_enabled} disabled={removeKey} aria-describedby="ai-import-info" onChange={event => { setSettings({...settings, import_enabled:event.target.checked}); setMessage(''); }} />Teilungserklärungen importieren sowie {list(sent)} auswerten</label>
            <p id="ai-import-info" className="text-sm text-muted-foreground mt-2">Ist dieser Schalter an, übermittelt Verwalino hochgeladene Teilungserklärungen sowie {list(sent)} ohne strukturierte E-Rechnungsdaten (zum Beispiel Scans) an den gewählten Anbieter. {invoices ? 'Rechnungen' : 'E-Rechnungen'} im Format XRechnung oder ZUGFeRD werden direkt gelesen und nicht übermittelt. {list(sent)} werden erst übermittelt, wenn Ihre Organisation den aktuellen Bestätigungstext angenommen hat. Jede Anfrage zählt zum monatlichen Limit; was {invoices && inbox ? 'ein Beleg oder ein Dokument im Posteingang' : invoices ? 'ein Beleg' : 'ein Dokument im Posteingang'} verbraucht hat, steht dort.</p>
          </div> : <label className="flex gap-2 items-start"><input type="checkbox" checked={settings.import_enabled} disabled={removeKey} onChange={event => { setSettings({...settings, import_enabled:event.target.checked}); setMessage(''); }} />Teilungserklärungen importieren</label>}
          <label className="flex gap-2 items-start"><input type="checkbox" checked={settings.qa_enabled} disabled={removeKey} onChange={event => { setSettings({...settings, qa_enabled:event.target.checked}); setMessage(''); }} />Fragen zum Objektwissen</label>
          <div>
            <Label htmlFor="ai-monthly-limit">Monatliches Limit in USD (optional)</Label>
            <Input id="ai-monthly-limit" type="number" min="0" step="0.01" inputMode="decimal" value={limit} onChange={event => { setLimit(event.target.value); setMessage(''); }} aria-describedby="ai-budget-info" />
            <p id="ai-budget-info" className="text-sm text-muted-foreground mt-2">Leer lassen für kein monatliches Limit. Das Limit gilt pro Kalendermonat (UTC) für Imports und Fragen zusammen, einschließlich laufender Reservierungen. Die Abrechnung erfolgt durch den gewählten Anbieter.</p>
            {settings.document_model && !documentKnown && <p className="text-sm mt-2">Für das Modell „Dokumente einlesen“ ist kein Preis bekannt. Das monatliche Limit wird für dieses Modell nicht durchgesetzt.</p>}
            {settings.model && !known && <p className="text-sm mt-2">Für das Modell „Informationen extrahieren“ ist kein Preis bekannt. Das monatliche Limit wird für dieses Modell nicht durchgesetzt.</p>}
          </div>
          <Button type="submit" disabled={busy}>{busy ? 'Wird gespeichert …' : 'KI-Einstellungen speichern'}</Button>
        </fieldset>
        <p role="status" className="text-sm mt-3">{message}</p>
      </form>}
  </section>;
}
