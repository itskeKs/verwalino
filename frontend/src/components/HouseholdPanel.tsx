import { useEffect, useRef, useState } from 'react';
import { getUnitHousehold, replaceUnitHousehold } from '../api/endpoints';
import type { UnitHouseholdRow } from '../api/types';
import { formatDate } from "../lib/format";

interface DraftRow { valid_from: string; persons: string; is_vacancy: boolean }

export function HouseholdPanel({ unitId, canWrite }: { unitId: string; canWrite: boolean }) {
  const [rows, setRows] = useState<UnitHouseholdRow[]>([]);
  const [draft, setDraft] = useState<DraftRow[]>([]);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const mutation = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    const current = ++generation.current;
    setLoading(true);
    getUnitHousehold(unitId).then(saved => {
      if (current !== generation.current) return;
      setRows(saved); setError(null);
    }).catch(err => {
      if (current === generation.current) setError(String(err));
    }).finally(() => { if (current === generation.current) setLoading(false); });
    return () => { generation.current = current + 1; };
  }, [unitId, retry]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!canWrite || mutation.current) return;
    if (draft.some(r => !r.valid_from || !/^\d+$/.test(r.persons) || Number(r.persons) > 2147483647)) {
      setError('Bitte Datum und eine nichtnegative ganze Personenzahl eingeben.'); return;
    }
    mutation.current = true; setSaving(true); setError(null); setStatus(null);
    const current = generation.current;
    try {
      const saved = await replaceUnitHousehold(unitId, draft.map(r => ({ ...r, persons: Number(r.persons) })));
      if (current !== generation.current) return;
      setRows(saved); setEditing(false); setStatus('Haushalt gespeichert.');
    } catch (err) {
      if (current === generation.current) setError(String(err));
    } finally {
      mutation.current = false;
      if (current === generation.current) setSaving(false);
    }
  }

  return <section className="card" aria-label="Personen im Haushalt">
    <h2>Personen im Haushalt</h2>
    <p className="muted">Belegung und Leerstand werden getrennt erfasst. Die Personenzahl gilt ab dem eingetragenen Datum bis zur nächsten Änderung derselben Art. Fehlende Angaben verhindern die Verteilung nach Personen.</p>
    <p className="muted">Für Leerstand wird 1 Person vorgeschlagen. Erst Speichern übernimmt den Wert; der Leerstandsanteil verbleibt beim Eigentümer.</p>
    {error && <p className="error-text" role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {loading ? <p>Haushalt wird geladen …</p> : editing ? <form onSubmit={save}>
      <fieldset disabled={saving || !canWrite} className="min-w-0 space-y-4">
        {draft.map((row, index) => <fieldset key={index} className="min-w-0 rounded-md border border-border p-3">
          <legend>{row.is_vacancy ? 'Leerstand' : 'Belegung'} {index + 1}</legend>
          <div className="form-grid">
            <label>{row.is_vacancy ? 'Personen im Leerstand ab' : 'Personen im Haushalt ab'}
              <input type="date" required value={row.valid_from} onChange={e => setDraft(previous => previous.map((r, i) => i === index ? { ...r, valid_from: e.target.value } : r))} />
            </label>
            <label>Personenzahl {row.is_vacancy ? 'Leerstand' : 'Belegung'}
              <input type="number" required min="0" max="2147483647" step="1" value={row.persons} onChange={e => setDraft(previous => previous.map((r, i) => i === index ? { ...r, persons: e.target.value } : r))} />
            </label>
          </div>
          <button type="button" className="btn btn--small" aria-label={`${row.is_vacancy ? 'Leerstand' : 'Belegung'} ${index + 1} entfernen`} onClick={() => setDraft(previous => previous.filter((_, i) => i !== index))}>Entfernen</button>
        </fieldset>)}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" onClick={() => setDraft(previous => [...previous, { valid_from: '', persons: '', is_vacancy: false }])}>Belegung hinzufügen</button>
          <button type="button" className="btn" onClick={() => setDraft(previous => [...previous, { valid_from: '', persons: '1', is_vacancy: true }])}>Leerstand hinzufügen</button>
          <button type="submit" className="btn btn--primary">{saving ? 'Haushalt wird gespeichert …' : 'Haushalt speichern'}</button>
          <button type="button" className="btn" onClick={() => { setEditing(false); setError(null); }}>Abbrechen</button>
        </div>
      </fieldset>
    </form> : <>
      {rows.length ? <ul className="mb-3 space-y-1">{rows.map(r => <li key={`${r.is_vacancy}:${r.valid_from}`}>
        {r.is_vacancy ? 'Leerstand: ' : ''}{r.persons.toLocaleString('de-DE')} {r.persons === 1 ? 'Person' : 'Personen'} ab {formatDate(r.valid_from)}
      </li>)}</ul> : !error && <p>Keine Haushaltsangaben gespeichert.</p>}
      {error ? <button type="button" className="btn" onClick={() => setRetry(value => value + 1)}>Haushalt erneut laden</button> : canWrite && <button type="button" className="btn" onClick={() => {
        setDraft(rows.map(r => ({ ...r, persons: String(r.persons) }))); setEditing(true); setStatus(null);
      }}>Haushalt bearbeiten</button>}
    </>}
  </section>;
}
