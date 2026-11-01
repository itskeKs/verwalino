import { ReserveEvidenceFields } from './ReserveEvidenceFields';
import { useEffect, useState } from 'react';
import { addReserveOverride, getReserveLedger, listCostEntries, listPayments, listRegisterEntries } from '../api/endpoints';
import type { CostEntry, Payment, RegisterEntry, ReserveComponent, ReserveLedger, ReserveOverride } from '../api/types';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './ui/dialog';

const euro = (amount: string) => new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(Number(amount));
const day = (value: string) => value.split('-').reverse().join('.');
const labels: Record<string, string> = { PLANNED: 'Geplant (Soll)', CONTRIBUTION: 'Gebuchte Zuführung', WITHDRAWAL: 'Finanzierte Entnahme',
  OVERRIDE: 'Begründete Korrektur', OPENING: 'Anfangsbestand', ADJUSTMENT: 'Sonstige Korrektur', FUNDING: 'Finanzierte Entnahme verknüpfen',
  LATE_CASH: 'Nachträgliche Buchungen zum abgeschlossenen Jahr', UNASSIGNED: 'Ohne Planbezug' };
const componentLabels: Record<string, string> = { ...labels, WITHDRAWAL: 'Entnahmekorrektur' };

export function ReserveLedgerPanel({ runId, propertyId, status, onChanged, disabled = false, refreshKey = 0 }: {
  runId: string; propertyId: string; status: string; onChanged: () => void; disabled?: boolean; refreshKey?: number;
}) {
  const [ledger, setLedger] = useState<ReserveLedger | null>(null);
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [open, setOpen] = useState(false);
  const [component, setComponent] = useState<ReserveComponent>('ADJUSTMENT');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [sources, setSources] = useState<{ payments: Payment[]; costs: CostEntry[]; register: RegisterEntry[] } | null>(null);
  const [payment, setPayment] = useState('');
  const [cost, setCost] = useState('');
  const [register, setRegister] = useState('');

  useEffect(() => {
    let active = true;
    setLedger(null); setError('');
    getReserveLedger(runId).then(value => { if (active) setLedger(value); })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : 'Rücklagenbuch konnte nicht geladen werden.'); });
    return () => { active = false; };
  }, [runId, status, version, refreshKey]);

  useEffect(() => {
    if (!open || component !== 'FUNDING') return;
    let active = true;
    setSources(null); setFormError('');
    Promise.all([listPayments({ status: 'booked' }), listCostEntries({ property_id: propertyId, status: 'confirmed' }), listRegisterEntries(propertyId)])
      .then(([payments, costs, register]) => { if (active) setSources({ payments: payments.filter(p => p.property_id === propertyId && p.payment_type === 'RUECKLAGENENTNAHME' && p.direction === 'OUT' && !p.reverses_id), costs, register }); })
      .catch(() => { if (active) setFormError('Finanzierungsbelege konnten nicht geladen werden. Bitte versuchen Sie es erneut.'); });
    return () => { active = false; };
  }, [open, component, propertyId]);

  async function save(event: React.FormEvent) {
    event.preventDefault(); setFormError('');
    const raw = amount.trim();
    const normalized = (/^-?\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(raw) ? raw.replaceAll('.', '') : raw).replace(',', '.');
    if (!reason.trim() || (component !== 'FUNDING' && !/^-?\d+(\.\d{1,2})?$/.test(normalized))) {
      setFormError('Bitte geben Sie einen Betrag mit höchstens zwei Nachkommastellen und eine Begründung ein.'); return;
    }
    if ((component === 'FUNDING' || component === 'WITHDRAWAL' || component === 'ADJUSTMENT' || (component === 'CONTRIBUTION' && !payment)) && (!cost || !register)) {
      setFormError('Bitte verknüpfen Sie einen bestätigten Kostenbeleg und einen Beschluss aus der Beschluss-Sammlung.'); return;
    }
    const body: ReserveOverride = { component, amount: component === 'FUNDING' ? '0.00' : normalized, reason: reason.trim() };
    if (component === 'WITHDRAWAL' || component === 'ADJUSTMENT' || (component === 'CONTRIBUTION' && !payment)) Object.assign(body, { cost_entry_id: cost, register_entry_id: register });
    if (component === 'FUNDING') Object.assign(body, { payment_id: payment, cost_entry_id: cost, register_entry_id: register });
    else if (component === 'CONTRIBUTION' && payment) body.payment_id = payment;
    setSaving(true);
    try {
      await addReserveOverride(runId, body);
      setOpen(false); setAmount(''); setReason(''); setPayment(''); setVersion(v => v + 1); onChanged();
    } catch (err) { setFormError(err instanceof Error ? err.message : 'Korrektur konnte nicht gespeichert werden.'); }
    finally { setSaving(false); }
  }

  return <section className="card" style={{ marginBottom: '1rem' }} aria-label="Rücklagenbuch">
    <h2>Erhaltungsrücklage · Rücklagenbuch</h2>
    <p>Ist-Bewegungen nach Wertstellung. Geplante Zuführungen erhöhen den verfügbaren Bestand erst mit der Zahlung.</p>
    {error && <p role="alert">{error} <button className="btn btn--secondary" onClick={() => setVersion(v => v + 1)}>Erneut laden</button></p>}
    {!ledger && !error && <p>Rücklagenbuch wird geladen…</p>}
    {ledger && <>
      {ledger.frozen && <p>Finalisierter Rücklagenstand · unveränderbarer Snapshot{ledger.booking_basis === 'LEGACY_SNAPSHOT' ? ' (historische manuelle Erfassung)' : ''}.</p>}
      {ledger.overdrawn && <p role="alert" className="error-text">Die Rücklage ist überzogen. Bitte prüfen Sie die Finanzierung und die Belege.</p>}
      {ledger.entries.some(entry => entry.kind === 'UNASSIGNED') && <p role="alert">Zahlungen ohne Planbezug sind noch nicht der Rücklage zugeordnet. Bitte ergänzen Sie einen finalisierten Wirtschaftsplan oder erfassen Sie eine begründete Zuordnung vor der Berechnung.</p>}
      {!!ledger.unlinked_payment_ids?.some(id => !ledger.entries.some(entry => entry.payment_id === id && entry.kind === 'UNASSIGNED')) && <p role="alert">Für Rücklagenentnahmen fehlen Kostenbeleg und Beschluss. Bitte verknüpfen Sie diese vor der Berechnung.</p>}
      <dl className="form-grid">
        {([['Anfangsbestand', ledger.opening_balance], ['Geplante Zuführung (Soll)', ledger.planned_contributions_total],
          ['Tatsächliche Zuführung (Ist)', ledger.contributions_total], ['Entnahmen', ledger.withdrawals_total],
          ['Sonstige Korrekturen', ledger.overrides_total],
          ...(ledger.late_cash_total && ledger.late_cash_total !== '0.00' ? [[labels.LATE_CASH, ledger.late_cash_total]] : []),
          ['Endbestand', ledger.closing_balance]] as const).map(([label, value]) =>
          <div key={label}><dt>{label}</dt><dd style={{ margin: 0, fontFamily: 'var(--mono)' }}>{value === null ? 'Historisch nicht erfasst' : euro(value)}</dd></div>)}
      </dl>
      <div>
        <table className="data-table reserve-ledger-table"><thead><tr><th>Datum</th><th>Art / Beleg</th><th>Betrag</th></tr></thead>
          <tbody>{ledger.entries.map((entry, index) => <tr key={entry.id ?? `${entry.kind}-${entry.date}-${index}`}>
            <td style={{ whiteSpace: 'nowrap' }}>{day(entry.date)}</td>
            <td style={{ overflowWrap: 'anywhere' }}>{labels[entry.kind] ?? entry.kind}{entry.component && ` · ${(entry.kind === 'OVERRIDE' ? componentLabels : labels)[entry.component] ?? entry.component}`}
              {entry.reason && <div style={{ overflowWrap: 'anywhere' }}>{entry.reason}</div>}
              {entry.owner_name && <div>Eigentümer: {entry.owner_name}</div>}
              {entry.payment_id && <div><a className="link" href={`/zahlungen/${entry.payment_id}`}>Zahlung: {entry.payment_note || `${day(entry.date)} · ${euro(entry.amount)}`}</a></div>}
              {entry.unit_id && <div>Einheit: {entry.unit_number ?? 'Nicht verfügbar'}</div>}
              {entry.cost_entry_id && <div><a className="link" href={`/kosten/${entry.cost_entry_id}`}>Kostenbeleg: {entry.cost_document_number || 'Beleg öffnen'}</a></div>}
              {entry.register_entry_id && <div><a className="link" href={`/beschluss-sammlung?property_id=${propertyId}#beschluss-${entry.register_entry_id}`}>Beschluss{entry.register_sequence_number ? ` Nr. ${entry.register_sequence_number}` : ' öffnen'}</a></div>}
            </td><td style={{ whiteSpace: 'nowrap', fontFamily: 'var(--mono)' }}>{euro(entry.amount)}</td>
          </tr>)}</tbody></table>
      </div>
      {ledger.entries.length === 0 && <p>Keine Einzelbewegungen in diesem Rücklagenstand.</p>}
      {status === 'draft' && !ledger.frozen && <button className="btn btn--secondary" disabled={disabled} onClick={() => setOpen(true)}>Korrektur erfassen</button>}
    </>}
    <Dialog open={open} onOpenChange={value => { if (!saving) setOpen(value); }}><DialogContent style={{ maxHeight: '90vh', overflowY: 'auto', width: 'calc(100% - 2rem)' }}>
      <DialogTitle>Rücklagenkorrektur</DialogTitle>
      <DialogDescription>Erfassen Sie eine begründete Differenz. Positive Entnahmen vermindern den Bestand; sonstige Korrekturen werden mit Vorzeichen addiert.</DialogDescription>
      <form onSubmit={save}><fieldset disabled={saving || disabled} className="form-grid" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
        <label>Art<select aria-label="Art" value={component} onChange={e => { setComponent(e.target.value as ReserveComponent); setPayment(''); setCost(''); setRegister(''); }}>
          {(['ADJUSTMENT', 'OPENING', 'CONTRIBUTION', 'WITHDRAWAL', 'FUNDING'] as const).map(kind => <option key={kind} value={kind}>{componentLabels[kind]}</option>)}
        </select></label>
        {component === 'CONTRIBUTION' && ledger?.entries.some(entry => entry.kind === 'UNASSIGNED') &&
          <label>Zahlung ohne Planbezug<select value={payment} onChange={e => setPayment(e.target.value)}>
            <option value="">Allgemeine Zuführungskorrektur</option>
            {ledger.entries.filter(entry => entry.kind === 'UNASSIGNED').map(entry =>
              <option key={entry.payment_id} value={entry.payment_id}>{day(entry.date)} · {euro(entry.amount)} · {entry.owner_name || entry.payment_note || 'Ohne Eigentümerzuordnung'}</option>)}
          </select></label>}
        {component === 'FUNDING' ? sources ? <>
          <label>Gebuchte Auszahlung *<select aria-label="Gebuchte Auszahlung *" required value={payment} onChange={e => setPayment(e.target.value)}><option value="">Bitte wählen</option>{sources.payments.map(p => <option key={p.id} value={p.id}>{day(p.value_date)} · {euro(p.amount)}{p.note ? ` · ${p.note}` : ''}</option>)}</select></label>
          <label>Bestätigter Kostenbeleg *<select aria-label="Bestätigter Kostenbeleg *" required value={cost} onChange={e => setCost(e.target.value)}><option value="">Bitte wählen</option>{sources.costs.map(c => <option key={c.id} value={c.id}>{c.document_number || day(c.invoice_date)} · {euro(c.gross_amount)}</option>)}</select></label>
          <label>Beschluss-Sammlung *<select aria-label="Beschluss-Sammlung *" required value={register} onChange={e => setRegister(e.target.value)}><option value="">Bitte wählen</option>{sources.register.map(r => <option key={r.id} value={r.id}>Nr. {r.sequence_number} · {r.wording.slice(0, 120)}</option>)}</select></label>
        </> : <p>Finanzierungsbelege werden geladen…</p> : <label>Betrag *<input required inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} /></label>}
        {(component === 'WITHDRAWAL' || component === 'ADJUSTMENT' || (component === 'CONTRIBUTION' && !payment)) &&
          <ReserveEvidenceFields propertyId={propertyId} cost={cost} register={register} onChange={(cost, register) => { setCost(cost); setRegister(register); }} />}
        {component === 'OPENING' && <p>Ein Import des Anfangsbestands ist nur im ersten Rücklagenjahr zulässig. Bitte begründen Sie den übernommenen Bestand.</p>}
        <label>Begründung *<textarea required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
        {formError && <p role="alert" className="error-text">{formError}</p>}
        <div className="form-actions"><button className="btn btn--primary" disabled={component === 'FUNDING' && !sources} type="submit">{saving ? 'Speichern…' : 'Speichern'}</button>
          <button className="btn btn--secondary" type="button" onClick={() => setOpen(false)}>Abbrechen</button></div>
      </fieldset></form>
    </DialogContent></Dialog>
  </section>;
}
