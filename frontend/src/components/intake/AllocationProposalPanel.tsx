import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { allocationProposalsApi, displayCents, exactCents, type AllocationCategory, type AllocationCommand, type AllocationHistory, type AllocationPreview } from '../../api/allocationProposals';

type Line = { key: string; category: string; description: string; gross: string; vat: string };
type Draft = { preview: AllocationPreview; expected: string | null; lines: Line[]; reason: string; acknowledged: boolean };
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const blank = (gross = '', vat = ''): Line => ({ key: crypto.randomUUID(), category: '', description: '', gross, vat });
function fresh(preview: AllocationPreview, history: AllocationHistory): Draft {
  return { preview, expected: history.items[0]?.id ?? null, lines: [blank(preview.gross_amount, preview.vat_amount ?? '')], reason: '', acknowledged: false };
}
function reviewed(draft: Draft, categories: AllocationCategory[]): AllocationCommand | null {
  const header = exactCents(draft.preview.gross_amount); const headerVat = draft.preview.vat_amount === null ? null : exactCents(draft.preview.vat_amount);
  let total = 0n; let totalVat = 0n;
  const lines: AllocationCommand['lines'] = [];
  for (const line of draft.lines) {
    const category = categories.find(item => item.id === line.category && item.is_active); const gross = exactCents(line.gross); const vat = line.vat.trim() ? exactCents(line.vat) : null;
    if (!category || !line.description.trim() || gross === null || gross === 0n || header === null || (gross < 0n) !== (header < 0n)
      || (headerVat === null ? !!line.vat.trim() : vat === null)
      || (vat !== null && ((vat !== 0n && (vat < 0n) !== (gross < 0n)) || (vat < 0n ? -vat : vat) > (gross < 0n ? -gross : gross)))) return null;
    total += gross; totalVat += vat ?? 0n;
    lines.push({ cost_category_id: category.id, cost_category_plan_version: category.plan_version, description: line.description.trim(),
      gross_amount: line.gross.trim().replace(',', '.'), vat_amount: headerVat === null ? null : line.vat.trim().replace(',', '.') });
  }
  if (!draft.acknowledged || !draft.reason.trim() || !lines.length || lines.length > 100 || total !== header || (headerVat !== null && totalVat !== headerVat)) return null;
  return { context_decision_id: draft.preview.context_decision_id, lines, reason: draft.reason.trim(), allocation_reviewed: true };
}
export function AllocationProposalPanel({ logicalId, contextDecisionId, ready, parentBusy, parentGeneration, onDenied }: {
  logicalId: string; contextDecisionId: string | null; ready: boolean; parentBusy: boolean; parentGeneration: number; onDenied: () => void;
}) {
  const [view, setView] = useState<{ history: AllocationHistory; preview: AllocationPreview | null; categories: AllocationCategory[]; authorizedDraft: string | null } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(true);
  const [sending, setSending] = useState(false); const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ expected: string | null; key: string; body: AllocationCommand } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const draftDecision = draft?.preview.context_decision_id ?? null;
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    Promise.all([allocationProposalsApi.history(logicalId, controller.signal).catch(failure => {
      if (!controller.signal.aborted && failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onDenied(); throw failure;
    }), allocationProposalsApi.categories(controller.signal), contextDecisionId ? allocationProposalsApi.preview(contextDecisionId, controller.signal) : Promise.resolve(null),
    contextDecisionId && draftDecision && contextDecisionId !== draftDecision ? allocationProposalsApi.preview(draftDecision, controller.signal) : Promise.resolve(null)])
      .then(([history, categories, preview, retained]) => { if (!controller.signal.aborted) {
        setView({ history, categories, preview, authorizedDraft: retained?.context_decision_id ?? preview?.context_decision_id ?? null });
        if (preview && ready) setDraft(prior => prior ?? fresh(preview, history));
      } }).catch(failure => { if (!controller.signal.aborted) {
        setView(null); setError('Kostenaufteilungen derzeit nicht verfügbar. Bitte erneut laden.');
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { pending.current = null; setSubmitted(false); setDraft(null); }
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, contextDecisionId, draftDecision, ready, generation, parentGeneration, onDenied]);
  const blocked = busy || parentBusy || sending;
  const canPropose = ready && !!view?.preview?.can_propose;
  const authorized = !!draft && view?.authorizedDraft === draft.preview.context_decision_id;
  const stale = conflict || !!draft && (draft.preview.context_decision_id !== contextDecisionId || draft.expected !== (view?.history.items[0]?.id ?? null));
  const amounts = draft?.lines.map(line => exactCents(line.gross)) ?? [];
  const sum = amounts.every(value => value !== null) ? amounts.reduce<bigint>((total, value) => total + value!, 0n) : null;
  const vats = draft?.lines.map(line => exactCents(line.vat)) ?? [];
  const vatSum = vats.every(value => value !== null) ? vats.reduce<bigint>((total, value) => total + value!, 0n) : null;
  function edit(index: number, field: keyof Line, value: string) { if (draft) setDraft({ ...draft, acknowledged: false, lines: draft.lines.map((line, i) => i === index ? { ...line, [field]: value } : line) }); }
  async function save() {
    if (blocked || !view || !draft || !authorized || (!pending.current && (!canPropose || stale))) return;
    if (!pending.current) {
      const body = reviewed(draft, view.categories); if (!body) { setError('Bitte Kostenarten, Beschreibungen, Vorzeichen und exakte Brutto-/Umsatzsteuersummen prüfen.'); return; }
      pending.current = { expected: draft.expected, key: crypto.randomUUID(), body };
    }
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await allocationProposalsApi.create(logicalId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if ([401, 403, 404].includes(failure.status)) { setView(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Die Aufteilung oder ihre Grundlage wurde geändert. Ihre Eingaben bleiben erhalten.' : 'Aufteilung nicht gespeichert. Bitte Zeilen und Berechtigung prüfen.'); setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Aufteilung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Kostenaufteilung vorschlagen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Kostenaufteilung vorschlagen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Aufteilungen aktualisieren</button></div>
    <p>Teilen Sie den geprüften Belegbetrag auf Kostenarten innerhalb dieses Abrechnungskreises auf. Alle Zeilen müssen zusammen genau den Belegbetrag ergeben.</p>
    <p>Dies speichert einen Vorschlag. Kostenübernahme, Umlagefähigkeit, Steuern und Dubletten benötigen weitere Prüfungen. Eine Aufteilung auf mehrere Objekte ist hier noch nicht möglich.</p>
    {busy && <p role="status">Lade Kostenaufteilungen…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {view && <>
      {!view.history.items.length && <p>Noch keine lesbare Kostenaufteilung.</p>}
      {view.history.items.map((proposal, index) => <details key={proposal.id} open={index === 0} className="min-w-0 space-y-2"><summary className="cursor-pointer">Aufteilungsvorschlag · Version {proposal.revision_number} · {new Date(proposal.created_at).toLocaleString('de-DE')}</summary>
        <p>Brutto: {proposal.gross_amount} EUR · Umsatzsteuer: {proposal.vat_amount ?? 'unbekannt'} · Keine Kostenfreigabe</p>
        <ol className="space-y-2">{proposal.lines.map(line => <li key={line.id} className="break-words">{line.position}. {line.category_label} · {line.description} · {line.gross_amount} EUR · USt: {line.vat_amount ?? 'unbekannt'}</li>)}</ol>
        <p className="break-words">{proposal.reason}</p>{proposal.context_decision_id !== contextDecisionId && <p>Frühere Kontextgrundlage; nicht automatisch übernommen.</p>}</details>)}
      {view.history.next_before_revision && <p>Hier werden die letzten 20 lesbaren Aufteilungen angezeigt.</p>}
      {!canPropose && <p>Für einen neuen Vorschlag wird eine aktuelle, berechtigte Kontextprüfung benötigt.</p>}
      {draft && authorized && (canPropose || submitted) && <>
        {stale && !submitted && <div className="space-y-2"><p>Prüfen Sie die neue Grundlage. Beim Übernehmen werden Ihre Zeilen durch einen neuen Entwurf ersetzt.</p><button className="btn" disabled={blocked || !canPropose} onClick={() => { if (view.preview) { setDraft(fresh(view.preview, view.history)); setConflict(false); setError(null); } }}>Neue Aufteilungsgrundlage übernehmen</button></div>}
        <p className="break-words">{draft.preview.scope_label} · Geprüfter Beleg: {draft.preview.gross_amount} EUR · Umsatzsteuer: {draft.preview.vat_amount ?? 'unbekannt'}</p>
        <p aria-live="polite">Zeilensumme: {sum === null ? 'unvollständig' : displayCents(sum) + ' EUR'} · Soll: {draft.preview.gross_amount} EUR</p>
        <p aria-live="polite">Umsatzsteuersumme: {draft.preview.vat_amount === null ? 'unbekannt; alle Zeilen bleiben unbekannt' : `${vatSum === null ? 'unvollständig' : displayCents(vatSum) + ' EUR'} · Soll: ${draft.preview.vat_amount} EUR`}</p>
        <fieldset disabled={blocked || submitted || !canPropose} className="space-y-4 min-w-0">
          {draft.lines.map((line, index) => <div key={line.key} className="space-y-2 min-w-0 rounded-md border border-input p-3"><h3>Zeile {index + 1}</h3>
            <label className="block" htmlFor={`allocation-category-${line.key}`}>Kostenart · Zeile {index + 1}</label><select id={`allocation-category-${line.key}`} className={inputClass} value={line.category} onChange={event => edit(index, 'category', event.target.value)}><option value="">Bitte wählen</option>{view.categories.map(category => <option key={category.id} value={category.id}>{category.code} · {category.label}</option>)}</select>
            <label className="block" htmlFor={`allocation-description-${line.key}`}>Beschreibung · Zeile {index + 1}</label><input id={`allocation-description-${line.key}`} className={inputClass} maxLength={1000} value={line.description} onChange={event => edit(index, 'description', event.target.value)} />
            <div className="grid gap-3 md:grid-cols-2"><div><label htmlFor={`allocation-gross-${line.key}`}>Brutto · Zeile {index + 1}</label><input id={`allocation-gross-${line.key}`} className={inputClass} maxLength={16} value={line.gross} onChange={event => edit(index, 'gross', event.target.value)} /></div>
              <div><label htmlFor={`allocation-vat-${line.key}`}>Umsatzsteuer · Zeile {index + 1}</label><input id={`allocation-vat-${line.key}`} className={inputClass} maxLength={16} value={line.vat} disabled={draft.preview.vat_amount === null} placeholder={draft.preview.vat_amount === null ? 'unbekannt' : ''} onChange={event => edit(index, 'vat', event.target.value)} /></div></div>
            <button className="btn" disabled={draft.lines.length === 1} onClick={() => setDraft({ ...draft, acknowledged: false, lines: draft.lines.filter((_, i) => i !== index) })}>Zeile {index + 1} entfernen</button>
          </div>)}
          <button className="btn" disabled={draft.lines.length >= 100} onClick={() => setDraft({ ...draft, acknowledged: false, lines: [...draft.lines, blank()] })}>Zeile hinzufügen</button>
          <label className="block" htmlFor="allocation-reason">Begründung der Aufteilung in Kostenarten</label><textarea id="allocation-reason" className={inputClass} maxLength={2000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value, acknowledged: false })} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Zeilen, Kostenarten und Beträge am ausgewählten Original geprüft.</label>
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !canPropose || !draft.acknowledged || !draft.reason.trim()))} onClick={() => void save()}>{submitted ? 'Dieselbe Aufteilung erneut senden' : 'Aufteilungsvorschlag speichern'}</button>
      </>}
    </>}
  </section>;
}
