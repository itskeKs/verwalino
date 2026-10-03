import { useEffect, useRef, useState, type ReactNode } from 'react';
import { correctionPreviewApi, type CorrectionImpact, type CorrectionPreview } from '../../api/allocationAdoptions';

const titles: Record<string, string> = { BK_MIETER: 'Betriebskostenabrechnung', WEG_JAHRESABRECHNUNG: 'WEG-Jahresabrechnung', VERMOEGENSBERICHT: 'Vermögensbericht', WIRTSCHAFTSPLAN: 'Wirtschaftsplan' };
function destination(item: CorrectionImpact) {
  const base = item.kind === 'economic_plan' ? 'wirtschaftsplaene' : item.run_type === 'WEG_JAHRESABRECHNUNG' ? 'weg-abrechnungen' : item.run_type === 'VERMOEGENSBERICHT' ? 'vermoegensberichte' : 'abrechnungen';
  return `/${base}/${item.id}`;
}

export function AllocationCorrectionPreview({ allocationId, reviewId, parentBusy, parentGeneration, confirmation }: {
  allocationId: string; reviewId: string | null; parentBusy: boolean; parentGeneration: number; confirmation?: (preview: CorrectionPreview) => ReactNode;
}) {
  const [preview, setPreview] = useState<CorrectionPreview | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    request.current?.abort(); setPreview(null); setError(null); setBusy(false);
    return () => request.current?.abort();
  }, [allocationId, reviewId, parentBusy, parentGeneration]);
  async function load() {
    if (parentBusy || !reviewId || busy) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setBusy(true); setPreview(null); setError(null);
    try {
      const value = await correctionPreviewApi.get(allocationId, reviewId, controller.signal);
      if (!controller.signal.aborted) setPreview(value);
    } catch {
      if (!controller.signal.aborted) setError('Korrekturfolgen nicht vollständig verfügbar. Bitte Berechtigungen und Prüfgrundlage prüfen und erneut laden.');
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="min-w-0 space-y-3 border-t border-input pt-4" aria-label="Korrekturfolgen">
    <h3>Korrekturfolgen prüfen</h3>
    <p>Prüfen Sie eine neue vollständige Aufteilung und Behandlung dieses Belegs. Anschließend können Sie die möglichen Folgen mit den bisher bestätigten Teilkosten vergleichen.</p>
    <button className="btn" disabled={parentBusy || busy || !reviewId} onClick={() => void load()}>Korrekturvorschau laden</button>
    {busy && <p role="status">Prüfe Aufteilung und abhängige Finanzvorgänge…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {!parentBusy && preview && <>
      {!preview.can_review ? <p role="status">Vollständige Korrekturvorschau derzeit nicht möglich. Eine neue aktuelle Prüfung im selben Abrechnungskreis und zugängliche, auswertbare Folgebelege sind erforderlich.</p> : <>
        <div className="grid min-w-0 gap-4 md:grid-cols-2">{([['Bisher bestätigt', preview.old], ['Neu geprüft', preview.proposed]] as const).map(([title, amounts]) => amounts && <div className="min-w-0 space-y-2" key={title}>
          <h4>{title}</h4><p>{amounts.gross_amount} EUR · {amounts.costs.length} Teilkosten</p>
          <p>Leistungszeitraum: {amounts.service_period_from} bis {amounts.service_period_to}</p>
          <ol className="space-y-1">{amounts.costs.map(cost => <li key={cost.position}>Teil {cost.position}: {cost.gross_amount} EUR · Mieteranteil {cost.tenant_amount ?? 'nicht angegeben'} EUR</li>)}</ol>
        </div>)}</div>
        <h4>Betroffene Finanzvorgänge</h4>
        {preview.impacts.length === 0 ? <p>Für diese Prüfgrundlage wurden keine betroffenen Finanzvorgänge gefunden.</p> : <CorrectionImpactList items={preview.impacts} />}
        <p role="status">Nur Vorschau: Die bisherigen Kosten bleiben aktiv. Eine Korrektur wurde noch nicht bestätigt.</p>
        {confirmation?.(preview)}
      </>}
    </>}
  </section>;
}

export function CorrectionImpactList({ items }: { items: CorrectionImpact[] }) {
  return <ul className="space-y-4">{items.map(item => <li className="space-y-1" key={`${item.kind}:${item.id}`}>
          <a className="underline" href={destination(item)}>{titles[item.run_type] ?? 'Finanzvorgang'} · {item.period_start} bis {item.period_end}</a>
          <p>{item.status === 'draft' ? 'Entwurf' : 'Festgeschrieben'}{item.superseded_by_id ? ' · Nachfolger vorhanden; dessen Abschluss muss geprüft werden.' : ''}</p>
          {item.effects.exact_cost_consumption && <p>Bisherige Teilkosten sind in der gespeicherten Berechnung enthalten.</p>}
          {item.effects.potential_calculation_change && <p>Der bisherige oder neue Leistungszeitraum kann diese Berechnung beeinflussen.</p>}
          {item.effects.coverage_renewal && <p>Deckungserklärung nach der Korrektur erneuern. Dies allein belegt keinen falschen Betrag.</p>}
          {item.effects.source_evidence_review && <p>Eine Deckungserklärung verweist auf das bisher freigegebene Original; Belegnachweis erneut prüfen.</p>}
          <p>{item.follow_up === 'refresh_and_revalidate' ? 'Nach einer Korrektur: Eingaben prüfen, erforderliche Deckung erneuern und neu berechnen.' : 'Nach einer Korrektur: fachliche Gültigkeit und gegebenenfalls eine endgültige Ersatzabrechnung prüfen.'}</p>
        </li>)}</ul>;
}
