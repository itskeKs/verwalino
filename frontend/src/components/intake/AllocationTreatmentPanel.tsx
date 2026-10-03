import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { exactCents } from '../../api/allocationProposals';
import { allocationTreatmentsApi, distributionKeys, type TreatmentCommand, type TreatmentHistory, type TreatmentLine, type TreatmentPreview } from '../../api/allocationTreatments';

type Line = { allocation_line_id: string; expense_intent: string; tax_intent: string; tenant_treatment: string; tenant_amount: string; tenant_distribution_key: string; owner_distribution_key: string; documentary_basis: string };
type Draft = { preview: TreatmentPreview; expected: string | null; lines: Line[]; reason: string; acknowledged: boolean };
const inputClass = 'w-full min-w-0 rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const expenses = { operating_expense: 'Laufender Aufwand', reserve_related: 'Rücklagenbezug · weitere Prüfung', capitalization_related: 'Aktivierungsbezug · weitere Prüfung', unresolved: 'Noch ungeklärt' };
const taxes = { gross_without_input_vat: 'Bruttoaufwand ohne Vorsteueranspruch', input_vat_requested: 'Vorsteuerbehandlung angefragt · weitere Prüfung', unresolved: 'Noch ungeklärt' };
const tenants = { full: 'Vollständiger Zeilenbetrag', partial: 'Teilbetrag', none: 'Kein Mieteranteil', unresolved: 'Noch ungeklärt' };
const markers: Record<string, string> = {
  expense_unresolved: 'Aufwandsart ungeklärt', reserve_treatment_unsupported: 'Rücklagenbehandlung braucht weitere Prüfung', capitalization_treatment_unsupported: 'Aktivierung braucht weitere Prüfung',
  tax_unresolved: 'Steuerbehandlung ungeklärt', input_vat_treatment_unsupported: 'Vorsteuerbehandlung braucht weitere Prüfung', tenant_treatment_unresolved: 'Mieteranteil ungeklärt',
  owner_distribution_unresolved: 'Eigentümerverteilung ungeklärt', tenant_distribution_evidence_required: 'Nachweis zur Mieterverteilung erforderlich', owner_distribution_evidence_required: 'Nachweis zur Eigentümerverteilung erforderlich',
  category_expense_conflict: 'Aufwandsart widerspricht Rücklagenmerkmal der Kostenart', category_tenant_conflict: 'Mieteranteil widerspricht nicht umlagefähiger Kostenart',
};
function fresh(preview: TreatmentPreview, history: TreatmentHistory): Draft {
  return { preview, expected: history.items[0]?.id ?? null, reason: '', acknowledged: false, lines: preview.snapshot.lines.map(line => ({ allocation_line_id: line.allocation_line_id,
    expense_intent: '', tax_intent: '', tenant_treatment: '', tenant_amount: '', tenant_distribution_key: '', owner_distribution_key: '', documentary_basis: '' })) };
}
function reviewed(draft: Draft): TreatmentCommand | null {
  if (!draft.acknowledged || !draft.reason.trim()) return null;
  const lines: TreatmentLine[] = [];
  for (const line of draft.lines) {
    const basis = draft.preview.snapshot.lines.find(item => item.allocation_line_id === line.allocation_line_id);
    const amount = exactCents(line.tenant_amount); const gross = basis ? exactCents(basis.gross_amount) : null;
    if (!basis || !basis.category.is_active || gross === null || !(line.expense_intent in expenses) || !(line.tax_intent in taxes) || !(line.tenant_treatment in tenants) || !line.documentary_basis.trim()) return null;
    const key = distributionKeys.find(value => value === line.tenant_distribution_key) ?? null;
    if ((line.tenant_treatment === 'none' && amount !== 0n) || (line.tenant_treatment === 'full' && amount !== gross)
      || (line.tenant_treatment === 'partial' && (amount === null || amount === 0n || (amount < 0n) !== (gross < 0n) || (amount < 0n ? -amount : amount) >= (gross < 0n ? -gross : gross)))
      || (['full', 'partial'].includes(line.tenant_treatment) && !key)
      || (draft.preview.snapshot.scope_type === 'WEG' ? line.owner_distribution_key !== 'unresolved' && !distributionKeys.some(value => value === line.owner_distribution_key) : line.owner_distribution_key !== 'not_applicable')) return null;
    lines.push({ allocation_line_id: line.allocation_line_id, expense_intent: line.expense_intent as TreatmentLine['expense_intent'], tax_intent: line.tax_intent as TreatmentLine['tax_intent'],
      tenant_treatment: line.tenant_treatment as TreatmentLine['tenant_treatment'], tenant_amount: line.tenant_treatment === 'unresolved' ? null : line.tenant_amount.trim().replace(',', '.'),
      tenant_distribution_key: ['none', 'unresolved'].includes(line.tenant_treatment) ? null : key, owner_distribution_key: line.owner_distribution_key as TreatmentLine['owner_distribution_key'], documentary_basis: line.documentary_basis.trim() });
  }
  return { allocation_proposal_id: draft.preview.snapshot.allocation_proposal_id, preview_digest: draft.preview.snapshot_digest, reason: draft.reason.trim(), treatment_reviewed: true, lines };
}
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);

export function AllocationTreatmentPanel({ logicalId, allocationId, ready, parentBusy, parentGeneration }: {
  logicalId: string; allocationId: string | null; ready: boolean; parentBusy: boolean; parentGeneration: number;
}) {
  const [view, setView] = useState<{ history: TreatmentHistory; preview: TreatmentPreview | null; authorizedDraft: string | null } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(true);
  const [sending, setSending] = useState(false); const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ body: TreatmentCommand; expected: string | null; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const retainedId = draft?.preview.snapshot.allocation_proposal_id ?? null;
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    const current = allocationId ? allocationTreatmentsApi.preview(allocationId, controller.signal) : Promise.resolve(null);
    const retained = retainedId && allocationId && retainedId !== allocationId ? allocationTreatmentsApi.preview(retainedId, controller.signal) : current;
    Promise.allSettled([allocationTreatmentsApi.history(logicalId, undefined, controller.signal), current, retained]).then(results => {
      if (controller.signal.aborted) return;
      const [history, preview, old] = results;
      if (history.status === 'rejected' || old.status === 'rejected' || preview.status === 'rejected') {
        const historyDenied = history.status === 'rejected' && denied(history.reason); const oldDenied = old.status === 'rejected' && denied(old.reason);
        if (historyDenied || oldDenied) { pending.current = null; setSubmitted(false); setDraft(null); }
        // A denied new allocation must not erase an independently authorized unknown prior command.
        if (history.status === 'fulfilled' && old.status === 'fulfilled' && old.value) setView({ history: history.value, preview: null, authorizedDraft: old.value.snapshot.allocation_proposal_id });
        else setView(null);
        setError('Behandlungsprüfungen derzeit nicht vollständig verfügbar. Bitte erneut laden.');
      } else {
        setView({ history: history.value, preview: preview.value, authorizedDraft: old.value?.snapshot.allocation_proposal_id ?? null });
        if (preview.value && ready) setDraft(prior => prior ?? fresh(preview.value!, history.value));
      }
      setBusy(false);
    });
    return () => controller.abort();
  }, [logicalId, allocationId, retainedId, ready, parentGeneration, generation]);
  const blocked = busy || parentBusy || sending;
  const canReview = ready && !!view?.preview?.can_review;
  const authorized = !!draft && view?.authorizedDraft === draft.preview.snapshot.allocation_proposal_id;
  const stale = conflict || !!draft && (draft.preview.snapshot.allocation_proposal_id !== allocationId || draft.expected !== (view?.history.items[0]?.id ?? null)
    || !!view?.preview && draft.preview.snapshot_digest !== view.preview.snapshot_digest);
  function edit(index: number, field: keyof Line, value: string) {
    if (!draft) return;
    setDraft({ ...draft, acknowledged: false, lines: draft.lines.map((line, i) => i !== index ? line : { ...line, [field]: value,
      ...(field === 'tenant_treatment' ? { tenant_amount: '', tenant_distribution_key: '' } : {}) }) });
  }
  async function save() {
    if (blocked || !draft || !authorized || (!pending.current && (!canReview || stale))) return;
    if (!pending.current) {
      const body = reviewed(draft); if (!body) { setError('Bitte jede Behandlungsart, den exakten Mieterbetrag, Verteilungsschlüssel und Belegbezug ausdrücklich prüfen.'); return; }
      pending.current = { body, expected: draft.expected, key: crypto.randomUUID() };
    }
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await allocationTreatmentsApi.create(logicalId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (denied(failure)) { setDraft(null); setView(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Die Behandlungsgrundlage wurde geändert. Ihre Eingaben bleiben erhalten.' : 'Behandlungsprüfung nicht gespeichert. Bitte Angaben und Berechtigung prüfen.'); setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Behandlungsprüfung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  function select(index: number, field: keyof Line, label: string, options: Record<string, string>) {
    const id = `treatment-${field}-${draft!.lines[index].allocation_line_id}`;
    return <div><label htmlFor={id}>{label} · Zeile {index + 1}</label><select id={id} className={inputClass} value={draft!.lines[index][field]} onChange={event => edit(index, field, event.target.value)}><option value="">Bitte ausdrücklich wählen</option>{Object.entries(options).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></div>;
  }
  return <section className="min-w-0 space-y-4 border-t border-input pt-4" aria-label="Behandlung der Kosten prüfen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Behandlung der Kosten prüfen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Behandlung aktualisieren</button></div>
    <p>Erfassen Sie Ihre Prüfung für jede Zeile der Kostenaufteilung. Ungeklärte Fragen dürfen ausdrücklich offen bleiben. Diese Angaben erzeugen keine Kostenübernahme oder Freigabe.</p>
    {busy && <p role="status">Lade Behandlungsprüfungen…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {view && <>
      {!view.history.items.length && <p>Noch keine lesbare Behandlungsprüfung.</p>}
      {view.history.items.map((review, i) => <details className="min-w-0 space-y-2" key={review.id} open={i === 0}><summary className="cursor-pointer">Behandlungsprüfung · Version {review.revision_number} · {new Date(review.created_at).toLocaleString('de-DE')}</summary>
        {review.allocation_proposal_id !== allocationId && <p>Frühere Kostenaufteilung; keine automatische Übernahme.</p>}
        <ol className="space-y-3">{review.lines.map(line => <li key={line.id} className="break-words"><p>{review.snapshot.lines.find(basis => basis.allocation_line_id === line.allocation_line_id)?.category.label} · {expenses[line.expense_intent]} · {taxes[line.tax_intent]}</p>
          <p>Mieter: {tenants[line.tenant_treatment]} · {line.tenant_amount ?? 'Betrag ungeklärt'} {line.tenant_amount !== null && 'EUR'} · Schlüssel: {line.tenant_distribution_key ?? 'keiner'}; Eigentümer: {line.owner_distribution_key === 'not_applicable' ? 'nicht anwendbar' : line.owner_distribution_key === 'unresolved' ? 'ungeklärt' : line.owner_distribution_key}</p>
          <p>{line.documentary_basis}</p><ul>{line.markers.map(marker => <li key={marker}>{markers[marker] ?? 'Weitere Prüfung erforderlich'}</li>)}</ul>
          {!line.markers.length && <p>Angaben vollständig; keine rechtliche oder finanzielle Freigabe.</p>}</li>)}</ol><p className="break-words">{review.reason}</p></details>)}
      {view.history.next_before_revision && <p>Hier werden die letzten 20 lesbaren Behandlungsprüfungen angezeigt.</p>}
      {!canReview && <p>Eine aktuelle, berechtigte Kostenaufteilung wird benötigt.</p>}
      {draft && authorized && (canReview || submitted) && <>
        {stale && !submitted && <div><p>Die Grundlage hat sich geändert. Prüfen Sie die aktuelle Aufteilung erneut.</p><button className="btn" disabled={blocked || !canReview} onClick={() => { if (view.preview) { setDraft(fresh(view.preview, view.history)); setConflict(false); setError(null); } }}>Neue Behandlungsgrundlage übernehmen</button></div>}
        <p>Eigentümer- und Mieterverteilung sind getrennte Sichtweisen. Der Eigentümerbetrag bleibt der volle Zeilenbetrag; er wird nicht um den Mieteranteil vermindert.</p>
        <fieldset disabled={blocked || submitted || !canReview} className="space-y-4 min-w-0">
          {draft.lines.map((line, index) => { const basis = draft.preview.snapshot.lines[index]; return <div key={line.allocation_line_id} className="min-w-0 space-y-3 rounded-md border border-input p-3"><h3>Behandlung · Zeile {index + 1}</h3>
            <p className="break-words">{basis.category.code} · {basis.category.label} · {basis.gross_amount} EUR · USt: {basis.vat_amount ?? 'unbekannt'}</p>
            <details className="break-words"><summary>Kostenart zur Prüfung</summary><p>Version {basis.category.plan_version} · {basis.category.is_active ? 'aktiv' : 'inaktiv'} · {basis.category.cost_group} · Rücklagenbezug: {basis.category.reserve_relevant ? 'ja' : 'nein'}</p>
              <p>Hinterlegte Vorgaben, nicht vorausgewählt: Mieter {basis.category.tenant_apportionability_default}; Eigentümerabrechnung {basis.category.owner_billing_relevant_default ? 'relevant' : 'nicht relevant'}; Schlüssel Eigentümer {basis.category.owner_distribution_key_default ?? 'keiner'}, Mieter {basis.category.tenant_distribution_key_default ?? 'keiner'}.</p></details>
            {select(index, 'expense_intent', 'Aufwandsart', expenses)}{select(index, 'tax_intent', 'Steuerbehandlung', taxes)}{select(index, 'tenant_treatment', 'Mieteranteil', tenants)}
            {line.tenant_treatment && line.tenant_treatment !== 'unresolved' && <div><label htmlFor={`treatment-amount-${line.allocation_line_id}`}>Exakter Mieterbetrag · Zeile {index + 1}</label><input id={`treatment-amount-${line.allocation_line_id}`} className={inputClass} maxLength={16} value={line.tenant_amount} onChange={event => edit(index, 'tenant_amount', event.target.value)} /><p>{line.tenant_treatment === 'none' ? 'Kein Mieteranteil: ausdrücklich 0,00 eingeben.' : line.tenant_treatment === 'full' ? `Vollständiger Anteil: exakt ${basis.gross_amount} EUR eingeben.` : 'Teilbetrag mit gleichem Vorzeichen, kleiner als der volle Betrag.'}</p></div>}
            {['full', 'partial'].includes(line.tenant_treatment) && select(index, 'tenant_distribution_key', 'Mieterverteilung', Object.fromEntries(distributionKeys.map(key => [key, key])))}
            {select(index, 'owner_distribution_key', 'Eigentümerverteilung', draft.preview.snapshot.scope_type === 'WEG' ? { ...Object.fromEntries(distributionKeys.map(key => [key, key])), unresolved: 'Noch ungeklärt' } : { not_applicable: 'Für diesen Miet-Abrechnungskreis nicht anwendbar' })}
            <label className="block" htmlFor={`treatment-basis-${line.allocation_line_id}`}>Belegbezug der Behandlung · Zeile {index + 1}</label><textarea id={`treatment-basis-${line.allocation_line_id}`} className={inputClass} maxLength={2000} value={line.documentary_basis} onChange={event => edit(index, 'documentary_basis', event.target.value)} />
          </div>; })}
          <label className="block" htmlFor="treatment-reason">Begründung der Behandlungsprüfung</label><textarea id="treatment-reason" className={inputClass} maxLength={2000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value, acknowledged: false })} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Behandlung jeder Zeile anhand der Unterlagen geprüft; offene Fragen ausdrücklich erfasst.</label>
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !canReview || !draft.acknowledged || !draft.reason.trim()))} onClick={() => void save()}>{submitted ? 'Dieselbe Behandlungsprüfung erneut senden' : 'Behandlungsprüfung speichern'}</button>
      </>}
    </>}
  </section>;
}
