import { useEffect, useState } from 'react';
import { contextCandidatesApi, type ContextCandidate, type ContextCandidates, type ContextEvidence } from '../../api/contextCandidates';
const labels: Record<ContextEvidence['kind'], string> = { property_id: 'Beschriftete Objekt-ID', meter_number: 'Zählernummer', service_address: 'Beschriftete Leistungsadresse' };
const exclusions: Record<string, string> = { unsupported_scope: 'Dieser Kreis wird für die Übernahme noch nicht unterstützt.', missing_period: 'Ein vollständiger Leistungszeitraum fehlt.', period_outside_scope: 'Der vorgeschlagene Leistungszeitraum liegt außerhalb dieses Kreises.', hint_disagrees: 'Der Upload-Hinweis nennt einen anderen Kreis.' };
const statuses: Record<string, string> = { unsupported_source: 'Für diesen Beleg sind keine auswertbaren Textseiten verfügbar.', source_limit: 'Zu viele oder zu umfangreiche Kontextangaben. Bitte den Beleg manuell zuordnen.', comparison_limit: 'Der zugängliche Vergleichsbereich ist zu groß. Bitte den Kreis manuell auswählen.', no_labelled_evidence: 'Keine unterstützte beschriftete Kontextangabe gefunden.', no_matching_context: 'Keine passende zugängliche Zuordnung gefunden.' };
function Evidence({ item }: { item: ContextEvidence }) { return <p className="break-words">{labels[item.kind]} · Seite {item.page_number}{item.method === 'ocr' ? ' · Texterkennung' : ''}: „{item.quotation}“</p>; }
export function ContextCandidatesPanel({ logicalId, partitionId, extractionId, disabled, parentGeneration, onPick }: {
  logicalId: string; partitionId: string | null; extractionId: string | null; disabled: boolean; parentGeneration: number; onPick: (item: ContextCandidate) => void;
}) {
  const [view, setView] = useState<ContextCandidates | null>(null); const [error, setError] = useState(false); const [busy, setBusy] = useState(false); const [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setView(null); setError(false);
    if (!partitionId || !extractionId) { setBusy(false); return () => controller.abort(); }
    setBusy(true);
    contextCandidatesApi.get(logicalId, partitionId, extractionId, controller.signal).then(result => { if (!controller.signal.aborted) setView(result); })
      .catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, partitionId, extractionId, parentGeneration, generation]);
  if (!partitionId || !extractionId) return null;
  const exact = view?.logical_document_id === logicalId && view.partition_revision_id === partitionId && view.extraction_revision_id === extractionId;
  return <section className="min-w-0 space-y-3 border-t border-input pt-4" aria-label="Hinweise zur Objektzuordnung">
    <div className="flex flex-wrap justify-between gap-3"><h3>Hinweise zur Objektzuordnung</h3><button type="button" className="btn" disabled={busy || disabled} onClick={() => setGeneration(value => value + 1)}>Zuordnungshinweise aktualisieren</button></div>
    <p>Treffer aus beschrifteten Belegangaben unterstützen Ihre Prüfung. Auch ein einzelner Treffer muss am Original geprüft werden. Die Zeitraumprüfung verwendet ungeprüfte Extraktionswerte.</p>
    {busy && <p role="status">Vergleiche zugängliche Objektangaben…</p>}
    {error && <p role="alert">Zuordnungshinweise derzeit nicht verfügbar. Ihre manuelle Eingabe bleibt erhalten.</p>}
    {view && exact && <>
      {view.status !== 'manual_review' && <p>{statuses[view.status] ?? 'Bitte die Zuordnung manuell prüfen.'}</p>}
      {(view.conflicting_properties || view.ambiguous_scopes) && <p>Mehrere Objekte oder Abrechnungskreise kommen infrage. Vergleichen Sie alle Angaben und wählen Sie den Kreis ausdrücklich.</p>}
      {view.unmatched_evidence.length > 0 && <div className="space-y-2"><p>Weitere Belegangaben ohne passende zugängliche Zuordnung; mögliche Widersprüche prüfen:</p>{view.unmatched_evidence.map((item, index) => <Evidence key={index} item={item} />)}</div>}
      <ul className="space-y-4">{view.items.map(item => <li key={item.scope_id} className="min-w-0 space-y-2 border-b border-input pb-3">
        <h4 className="break-words">{item.property_name} · {item.scope_label} · {item.scope_type}</h4>
        {item.evidence.map((evidence, index) => <Evidence key={index} item={evidence} />)}
        {item.mappings?.map(mapping => <p className="break-words" key={mapping.id}>Gespeicherter Hinweis · Version {mapping.revision_number} · Leistungszeitraum {mapping.valid_from} bis {mapping.valid_to}. <a className="underline" href={`/zuordnungen/${mapping.id}`}>Zuordnung prüfen oder widerrufen</a></p>)}
        {item.meter_period_unverified && <p>Die Zählernummer belegt keine Gültigkeit im Leistungszeitraum und keine Zuständigkeit für das gesamte Objekt.</p>}
        {item.exclusions.map(reason => <p key={reason}>{exclusions[reason] ?? 'Weitere Prüfung erforderlich.'}</p>)}
        <button type="button" className="btn btn--wrap" disabled={disabled || busy || !view.can_prepare || item.exclusions.length > 0} onClick={() => onPick(item)}>Als manuelle Auswahl übernehmen: {item.scope_label}</button>
      </li>)}</ul>
    </>}
  </section>;
}
