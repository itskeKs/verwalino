import { useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { LetterDocument } from '../api/letters';
import type { Meter } from '../api/types';
import { listMeters, listMeterReadings } from '../api/endpoints';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';

function today() {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function failure(error: unknown) {
  if (!(error instanceof ApiError)) return 'Verbindung fehlgeschlagen. Bitte erneut versuchen.';
  if (error.status === 401) return 'Bitte melden Sie sich erneut an.';
  if (error.status === 403) return 'Keine Berechtigung für Vertragsdokumente.';
  if (error.status === 404) return 'Vertragsdaten oder Zähler nicht zugänglich.';
  if (error.status >= 500) return 'Vertragsdokument konnte nicht gespeichert oder geladen werden. Bitte versuchen Sie es erneut.';
  return error.message;
}

export function LeaseDocuments({ leaseId, propertyId, unitId, tenantLabel, canWrite, onReadingsSaved }: {
  leaseId: string; propertyId: string; unitId: string; tenantLabel: string; canWrite: boolean; onReadingsSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [asOf, setAsOf] = useState(today);
  const [handoverDate, setHandoverDate] = useState('');
  const [agreements, setAgreements] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [documents, setDocuments] = useState<LetterDocument[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const mutationLock = useRef(false);
  const requestId = useRef<string | null>(null);
  const [handoverOpen, setHandoverOpen] = useState(false);
  const [type, setType] = useState('Einzug');
  const [parties, setParties] = useState(tenantLabel);
  const [signatures, setSignatures] = useState('');
  const [notes, setNotes] = useState('');
  const [keys, setKeys] = useState<{ type: string; count: string }[]>([]);
  const [meters, setMeters] = useState<(Meter & { value: string })[]>([]);
  const [meterLoading, setMeterLoading] = useState(false);
  const [meterError, setMeterError] = useState<string | null>(null);

  function changed() { requestId.current = null; }

  async function loadMeters() {
    setMeterLoading(true); setMeterError(null);
    try {
      const items = await listMeters(propertyId, unitId);
      const rows = await Promise.all(items.map(async meter => {
        const readings = await listMeterReadings(meter.id);
        const latest = [...readings].sort((a, b) => {
          const day = b.reading_date.localeCompare(a.reading_date);
          if (day) return day;
          const first = BigInt(a.value.replace('.', '')), second = BigInt(b.value.replace('.', ''));
          return first < second ? 1 : first > second ? -1 : a.id.localeCompare(b.id);
        })[0];
        return { ...meter, value: latest?.value.replace('.', ',') ?? '' };
      }));
      setMeters(rows);
    } catch (error) { setMeterError(failure(error)); }
    finally { setMeterLoading(false); }
  }

  async function submitHandover(event: React.FormEvent) {
    event.preventDefault();
    if (mutationLock.current || !canWrite || meterLoading || meterError) return;
    mutationLock.current = true; setSaving(true); setError(null);
    try {
      requestId.current ??= crypto.randomUUID();
      const row = await api.post<LetterDocument>(`/api/leases/${encodeURIComponent(leaseId)}/handover`, {
        request_id: requestId.current, date: handoverDate, type, parties, signatures, notes,
        keys: keys.map(key => ({ type: key.type, count: Number(key.count) })),
        readings: meters.map(meter => ({ meter_id: meter.id, value: meter.value.trim().replace(',', '.') })),
      });
      setDocuments(previous => [row, ...previous.filter(item => item.id !== row.id)]);
      setHandoverOpen(false); onReadingsSaved();
    } catch (error) { setError(failure(error)); }
    finally { mutationLock.current = false; setSaving(false); }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mutationLock.current || !canWrite) return;
    mutationLock.current = true;
    setSaving(true); setError(null);
    try {
      requestId.current ??= crypto.randomUUID();
      const row = await api.post<LetterDocument>(`/api/leases/${encodeURIComponent(leaseId)}/contract`, {
        request_id: requestId.current, as_of: asOf, handover_date: handoverDate, agreements,
      });
      setDocuments(previous => [row, ...previous.filter(item => item.id !== row.id)]);
      setOpen(false);
    } catch (error) { setError(failure(error)); }
    finally { mutationLock.current = false; setSaving(false); }
  }

  async function loadHistory() {
    setHistoryOpen(true); setLoading(true); setHistoryError(null);
    try { setDocuments(await api.get<LetterDocument[]>(`/api/leases/${encodeURIComponent(leaseId)}/documents`)); }
    catch (error) { setHistoryError(failure(error)); }
    finally { setLoading(false); }
  }

  return <Card className="mt-4 min-w-0">
    <CardHeader><CardTitle className="break-words">Vertragsdokumente · {tenantLabel}</CardTitle></CardHeader>
    <CardContent><section className="min-w-0 space-y-3" aria-label="Vertragsdokumente">
    <div className="flex flex-wrap gap-2">
      {canWrite && <Button variant="outline" onClick={() => { setError(null); requestId.current = null; setOpen(true); }}>Mietvertrag erstellen</Button>}
      {canWrite && <Button variant="outline" onClick={() => { setError(null); changed(); setHandoverOpen(true); void loadMeters(); }}>Übergabeprotokoll</Button>}
      <Button variant="outline" disabled={loading} onClick={() => { void loadHistory(); }}>Vertragsdokumente anzeigen</Button>
    </div>
    {loading && <p role="status">Vertragsdokumente werden geladen…</p>}
    {historyError && <p role="alert" className="error-text">{historyError}</p>}
    {historyOpen && !loading && !historyError && documents.length === 0 && <p>Noch keine Vertragsdokumente.</p>}
    {documents.length > 0 && <ul className="space-y-2 break-words">
      {documents.map(row => <li key={row.id}><a className="link" href={row.pdf_url} target="_blank" rel="noopener noreferrer">{row.subject} · PDF öffnen</a></li>)}
    </ul>}
    <Dialog open={open} onOpenChange={next => { if (!mutationLock.current) setOpen(next); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogTitle>Mietvertrag erstellen</DialogTitle>
        <DialogDescription>Vorlage; rechtliche Prüfung erforderlich. Bitte prüfen Sie die Parteien und ergänzen Sie die freien Vertragsbedingungen. Die Beträge stammen aus dem Originalvertrag. Eigentümer bei Mietbeginn werden als Vermieter vorbelegt; fehlende Daten sind als Freitext markiert.</DialogDescription>
        <form onSubmit={event => { void submit(event); }} className="space-y-4">
          <fieldset disabled={saving} className="min-w-0 space-y-4">
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-issue-date`}>Dokumentdatum</Label><Input id={`${leaseId}-issue-date`} type="date" required value={asOf} onChange={event => { requestId.current = null; setAsOf(event.target.value); }} /></div>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-handover-date`}>Übergabedatum</Label><Input id={`${leaseId}-handover-date`} type="date" required value={handoverDate} onChange={event => { requestId.current = null; setHandoverDate(event.target.value); }} /></div>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-agreements`}>Freitext: weitere Vereinbarungen</Label><textarea id={`${leaseId}-agreements`} rows={5} maxLength={10000} className="w-full rounded-md border border-border p-3" value={agreements} onChange={event => { requestId.current = null; setAgreements(event.target.value); }} /></div>
          </fieldset>
          {error && <p role="alert" className="error-text">{error}</p>}
          <DialogFooter><Button type="button" variant="outline" disabled={saving} onClick={() => setOpen(false)}>Abbrechen</Button><Button type="submit" className="whitespace-normal" disabled={saving}>{saving ? 'Speichert…' : 'PDF erstellen und speichern'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={handoverOpen} onOpenChange={next => { if (!mutationLock.current) setHandoverOpen(next); }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogTitle>Übergabeprotokoll</DialogTitle>
        <DialogDescription>Bitte prüfen Sie die letzten Zählerstände und tragen Sie die Ablesungen zur Übergabe ein. Speichern erstellt ein unveränderliches PDF und erfasst die Ablesungen. Die Namen sind keine elektronische Signatur.</DialogDescription>
        <form onSubmit={event => { void submitHandover(event); }} className="space-y-4">
          <fieldset disabled={saving} className="min-w-0 space-y-4">
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-protocol-date`}>Übergabedatum</Label><Input id={`${leaseId}-protocol-date`} type="date" required value={handoverDate} onChange={event => { changed(); setHandoverDate(event.target.value); }} /></div>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-type`}>Art</Label><select id={`${leaseId}-type`} className="w-full rounded-md border border-border p-2" value={type} onChange={event => { changed(); setType(event.target.value); }}><option>Einzug</option><option>Auszug</option></select></div>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-parties`}>Anwesende Parteien</Label><textarea id={`${leaseId}-parties`} required rows={3} maxLength={4000} className="w-full rounded-md border border-border p-3" value={parties} onChange={event => { changed(); setParties(event.target.value); }} /></div>
            {meterLoading && <p role="status">Zählerstände werden geladen…</p>}
            {meterError && <div><p role="alert" className="error-text">{meterError}</p><Button type="button" variant="outline" onClick={() => { void loadMeters(); }}>Zähler erneut laden</Button></div>}
            {!meterLoading && !meterError && meters.length === 0 && <p>Dieser Einheit sind keine Zähler zugeordnet.</p>}
            {meters.map((meter, index) => <div className="grid gap-2 break-words" key={meter.id}><Label htmlFor={`${leaseId}-${meter.id}`}>Zählerstand {meter.meter_number}</Label><span className="text-sm text-muted-foreground">{meter.meter_type} · {meter.location}</span><Input id={`${leaseId}-${meter.id}`} inputMode="decimal" required value={meter.value} onChange={event => { changed(); setMeters(previous => previous.map((row, i) => i === index ? { ...row, value: event.target.value } : row)); }} /></div>)}
            <fieldset className="min-w-0 space-y-3"><legend className="font-medium">Schlüssel</legend>
              {keys.map((key, index) => <div className="grid gap-2 rounded-md border border-border p-3" key={index}>
                <Label htmlFor={`${leaseId}-key-${index}`}>Schlüsselart {index + 1}</Label><Input id={`${leaseId}-key-${index}`} required maxLength={100} value={key.type} onChange={event => { changed(); setKeys(previous => previous.map((row, i) => i === index ? { ...row, type: event.target.value } : row)); }} />
                <Label htmlFor={`${leaseId}-count-${index}`}>Anzahl {index + 1}</Label><Input id={`${leaseId}-count-${index}`} type="number" required min={0} max={1000} step={1} value={key.count} onChange={event => { changed(); setKeys(previous => previous.map((row, i) => i === index ? { ...row, count: event.target.value } : row)); }} />
                <Button type="button" variant="outline" onClick={() => { changed(); setKeys(previous => previous.filter((_, i) => i !== index)); }}>Schlüssel {index + 1} entfernen</Button>
              </div>)}
              <Button type="button" variant="outline" disabled={keys.length >= 100} onClick={() => { changed(); setKeys(previous => [...previous, { type: '', count: '1' }]); }}>Schlüssel hinzufügen</Button>
            </fieldset>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-notes`}>Mängel / Notizen</Label><textarea id={`${leaseId}-notes`} rows={4} maxLength={10000} className="w-full rounded-md border border-border p-3" value={notes} onChange={event => { changed(); setNotes(event.target.value); }} /></div>
            <div className="grid gap-2"><Label htmlFor={`${leaseId}-signatures`}>Unterschriften (Namen)</Label><textarea id={`${leaseId}-signatures`} required rows={3} maxLength={4000} className="w-full rounded-md border border-border p-3" value={signatures} onChange={event => { changed(); setSignatures(event.target.value); }} /></div>
          </fieldset>
          {error && <p role="alert" className="error-text">{error}</p>}
          <DialogFooter><Button type="button" variant="outline" disabled={saving} onClick={() => setHandoverOpen(false)}>Abbrechen</Button><Button type="submit" className="whitespace-normal" disabled={saving || meterLoading || !!meterError}>{saving ? 'Speichert…' : 'Protokoll und Ablesungen speichern'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
    </section></CardContent>
  </Card>;
}
