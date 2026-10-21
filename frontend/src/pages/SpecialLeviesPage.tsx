import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { createSpecialLevy, listSpecialLevies, listProperties, listUnits, listOwnerProfiles, listPersons } from "../api/endpoints";
import type { SpecialLevy, Property, Unit, OwnerProfile, Person } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";

function moneyInput(value: string): string | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(normalized) || !/[1-9]/.test(normalized)) return null;
  const [whole, fraction = ""] = normalized.split(".");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value && value >= "0001-01-01";
}
const errorText = (error: unknown) => error instanceof Error ? error.message : "Anfrage fehlgeschlagen. Bitte erneut versuchen.";

export default function SpecialLeviesPage() {
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [context, setContext] = useState<{ properties: Property[]; units: Unit[]; owners: OwnerProfile[]; persons: Person[] } | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextRevision, setContextRevision] = useState(0);
  const [propertyId, setPropertyId] = useState("");
  const [readScope, setReadScope] = useState<string | null>(null);
  const [prepareScope, setPrepareScope] = useState<string | null>(null);
  const [readValid, setReadValid] = useState(true);
  const [prepareValid, setPrepareValid] = useState(true);
  const [amount, setAmount] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [rows, setRows] = useState<SpecialLevy[]>([]);
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const alive = useRef(false);
  const generation = useRef(0);
  const listGeneration = useRef(0);
  const saving = useRef(false);
  const createdRows = useRef<SpecialLevy[]>([]);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    Promise.all([listProperties(), listUnits(), listOwnerProfiles(), listPersons()]).then(([properties, units, owners, persons]) => {
      if (current) { setContext({ properties, units, owners, persons }); setContextError(null); }
    }).catch(err => { if (current) setContextError(errorText(err)); });
    return () => { current = false; };
  }, [contextRevision]);

  useEffect(() => {
    const request = ++listGeneration.current;
    let current = true;
    if (!propertyId || !readScope || !readValid) return;
    listSpecialLevies(readScope, propertyId).then(items => {
      if (current && alive.current && request === listGeneration.current) {
        const confirmed = createdRows.current.filter(item => item.property_id === propertyId && item.accounting_scope_id === readScope);
        setRows([...confirmed, ...items.filter(item => !confirmed.some(created => created.id === item.id))]);
        setListError(null);
      }
    }).catch(err => {
      if (current && alive.current && request === listGeneration.current) setListError(errorText(err));
    }).finally(() => {
      if (current && alive.current && request === listGeneration.current) setLoading(false);
    });
    return () => { current = false; };
  }, [propertyId, readScope, readValid, revision]);

  function changeProperty(id: string) {
    generation.current++; listGeneration.current++;
    createdRows.current = [];
    setPropertyId(id); setReadScope(null); setPrepareScope(null); setRows([]);
    setReadValid(true); setPrepareValid(true); setLoading(false); setError(null); setListError(null);
  }
  function changeReadScope(id: string | null) {
    generation.current++; listGeneration.current++;
    setReadScope(id); setRows([]); setListError(null); setLoading(!!id);
  }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (saving.current || !canWrite || !propertyId || !prepareScope || !prepareValid) return;
    const normalized = moneyInput(amount);
    if (!normalized) { setError("Betrag muss positiv sein, mit höchstens 12 Vorkomma- und 2 Nachkommastellen."); return; }
    if (!validDate(dueDate)) { setError("Bitte ein gültiges Fälligkeitsdatum angeben."); return; }
    saving.current = true; setPending(true); setError(null);
    const request = generation.current;
    // A list started before this mutation must not remove its newly persisted result.
    listGeneration.current++;
    setLoading(false);
    try {
      const created = await createSpecialLevy({ property_id: propertyId, accounting_scope_id: prepareScope, amount: normalized, due_date: dueDate });
      if (!alive.current || request !== generation.current) return;
      listGeneration.current++;
      createdRows.current = [created, ...createdRows.current.filter(item => item.id !== created.id)];
      if (readScope === prepareScope) setRows(items => [created, ...items.filter(item => item.id !== created.id)]);
      else { setRows([created]); setReadScope(prepareScope); }
      setListError(null);
    } catch (err) {
      if (alive.current && request === generation.current) setError(errorText(err));
    } finally {
      saving.current = false;
      if (alive.current) setPending(false);
    }
  }
  function ownerLabel(id: string): string {
    const owner = context?.owners.find(item => item.id === id);
    const person = context?.persons.find(item => item.id === owner?.person_id);
    return person ? person.company_name || `${person.first_name} ${person.last_name}`.trim() : "Eigentümer nicht verfügbar";
  }
  function unitLabel(id: string): string {
    const unit = context?.units.find(item => item.id === id);
    return unit ? `Wohnung ${unit.number}${unit.location ? ` · ${unit.location}` : ""}` : "Einheit nicht verfügbar";
  }
  return <div>
    <h1>Sonderumlagen</h1>
    <p><Link to="/mietkonto">Eigentümerkonto</Link> · <Link to="/mahnungen">Mahnungen</Link></p>
    <p className="muted">Die Anteile werden beim Anlegen serverseitig nach MEA festgeschrieben. Fällige Beträge erscheinen im Eigentümerkonto und im bestehenden Mahnwesen.</p>
    {contextError && <div role="alert">{contextError} <button className="btn" onClick={() => setContextRevision(v => v + 1)}>Stammdaten erneut laden</button></div>}
    {!context && !contextError && <p role="status">Lade Stammdaten…</p>}
    <div className="card mb-4">
      <label>WEG-Objekt *<select aria-label="WEG-Objekt *" value={propertyId} disabled={!context} onChange={e => changeProperty(e.target.value)}>
        <option value="">-- WEG-Objekt wählen --</option>
        {context?.properties.filter(item => item.management_type === "WEG" || item.management_type === "MISCH").map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      {propertyId && <AccountingScopeSelector key={`read-${propertyId}`} propertyId={propertyId} kind="WEG" capability="read" value={readScope} onChange={changeReadScope} onValidityChange={setReadValid} />}
      <button className="btn" disabled={!propertyId || !readScope || !readValid || loading || pending} onClick={() => { setLoading(true); setListError(null); setRevision(v => v + 1); }}>Aktualisieren</button>
    </div>
    {canWrite && propertyId && <form className="card mb-4" onSubmit={create}>
      <h2>Sonderumlage anlegen</h2>
      <AccountingScopeSelector key={`prepare-${propertyId}`} propertyId={propertyId} kind="WEG" capability="prepare" value={prepareScope} onChange={id => { generation.current++; setPrepareScope(id); }} onValidityChange={setPrepareValid} />
      <label>Betrag (EUR) *<input aria-label="Betrag (EUR) *" type="text" inputMode="decimal" required value={amount} onChange={e => setAmount(e.target.value)} /></label>
      <label>Fällig am *<input aria-label="Fällig am *" type="date" required value={dueDate} onChange={e => setDueDate(e.target.value)} /></label>
      <button className="btn btn--primary" type="submit" disabled={pending || !prepareScope || !prepareValid || !context}>Sonderumlage anlegen</button>
      {pending && <p role="status">Sonderumlage wird angelegt…</p>}
    </form>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {listError && <p role="alert" className="error-text">{listError}</p>}
    {loading && <p role="status">Lade Sonderumlagen…</p>}
    {!readScope && <p>Bitte WEG-Objekt und Abrechnungskreis wählen.</p>}
    {readScope && !readValid && <p role="alert">Bitte einen verfügbaren Abrechnungskreis wählen.</p>}
    {readScope && readValid && !loading && !listError && rows.length === 0 && <p>Keine Sonderumlagen vorhanden.</p>}
    {readScope && readValid && !listError && rows.map(row => <section className="card mb-4" key={row.id}>
      <h2>Sonderumlage · {row.amount} EUR</h2><p>Fällig am {row.due_date}</p>
      <table className="data-table"><thead><tr><th>Eigentümer</th><th>Einheit</th><th>MEA</th><th>Anteil</th></tr></thead>
        <tbody>{row.allocations.map((item, index) => <tr key={`${item.owner_profile_id}-${item.unit_id}-${index}`}><td>{ownerLabel(item.owner_profile_id)}</td><td>{unitLabel(item.unit_id)}</td><td>{item.mea}</td><td>{item.amount} EUR</td></tr>)}</tbody>
      </table>
    </section>)}
  </div>;
}
