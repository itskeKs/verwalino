import type { DeclarationRunBudget as Run } from '../../api/types';

export function DeclarationRunBudget({ runs }: { runs: Run[] }) {
  if (!runs.length) return null;
  return <section className="card p-5 sm:p-6 my-5 min-w-0" aria-labelledby="declaration-run-budget-heading">
    <h2 id="declaration-run-budget-heading">Verbrauch je Importlauf</h2>
    <p className="text-sm text-muted-foreground mt-2">Anbieterangaben und vorsorglich reservierte Token werden getrennt ausgewiesen. Die Laufzeit stammt aus dem jeweiligen Lauf. Bekannte Kosten sind Schätzungen in USD; die Abrechnung erfolgt beim Anbieter.</p>
    {runs.map((run, index) => <div key={run.id} className="mt-4 border-t pt-4 min-w-0">
      <h3>Lauf {index + 1}</h3>
      <p className="text-sm break-words">{run.model}</p>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 mt-3 text-sm">
        {run.usage_status === 'UNKNOWN' ? <div><dt>Tokenverbrauch</dt><dd>Tokenverbrauch unbekannt</dd></div> : <>
          {(run.usage_status === 'REPORTED' || run.usage_status === 'PARTIAL') && <>
            <div><dt>{run.usage_status === 'PARTIAL' ? 'Gemeldete Eingabetoken' : 'Eingabetoken'}</dt><dd>{run.reported_input_tokens?.toLocaleString('de-DE')}</dd></div>
            <div><dt>{run.usage_status === 'PARTIAL' ? 'Gemeldete Ausgabetoken' : 'Ausgabetoken'}</dt><dd>{run.reported_output_tokens?.toLocaleString('de-DE')}</dd></div>
          </>}
          {(run.usage_status === 'RESERVED' || run.usage_status === 'PARTIAL') && <>
            <div><dt>{run.usage_status === 'PARTIAL' ? 'Konservativ erfasste Eingabetoken' : 'Reservierte Eingabetoken'}</dt><dd>{run.input_tokens.toLocaleString('de-DE')}</dd></div>
            <div><dt>{run.usage_status === 'PARTIAL' ? 'Konservativ erfasste Ausgabetoken' : 'Reservierte Ausgabetoken'}</dt><dd>{run.output_tokens.toLocaleString('de-DE')}</dd></div>
          </>}
        </>}
        <div><dt>Laufzeit</dt><dd>{Math.round(run.runtime_seconds).toLocaleString('de-DE')} s</dd></div>
        <div><dt>{run.usage_status === 'REPORTED' ? 'Geschätzte Kosten' : 'Konservative Kostenschätzung'}</dt><dd>{run.estimated_cost_usd === null ? 'kein Preis bekannt' : `${Number(run.estimated_cost_usd).toLocaleString('de-DE', {minimumFractionDigits:2, maximumFractionDigits:6})} USD`}</dd></div>
      </dl>
      {run.usage_status === 'RESERVED' && <p className="text-sm text-muted-foreground mt-3">Der tatsächliche Tokenverbrauch ist noch nicht bekannt. Die Reservierung bleibt bis zur Klärung vorsorglich im Budget erfasst.</p>}
      {run.usage_status === 'PARTIAL' && <p className="text-sm text-muted-foreground mt-3">Anbieterangaben liegen nur teilweise vor. Die konservativ erfassten Werte enthalten auch den weiterhin reservierten Anteil.</p>}
    </div>)}
  </section>;
}
