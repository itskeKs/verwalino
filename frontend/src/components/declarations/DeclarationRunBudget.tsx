import type { DeclarationRunBudget as Run } from '../../api/types';

export function DeclarationRunBudget({ runs }: { runs: Run[] }) {
  if (!runs.length) return null;
  return <section className="card p-5 sm:p-6 my-5 min-w-0" aria-labelledby="declaration-run-budget-heading">
    <h2 id="declaration-run-budget-heading">Verbrauch je Importlauf</h2>
    <p className="text-sm text-muted-foreground mt-2">Tokenverbrauch und Laufzeit stammen aus dem jeweiligen Lauf. Bekannte Kosten sind Schätzungen in USD; die Abrechnung erfolgt beim Anbieter.</p>
    {runs.map((run, index) => <div key={run.id} className="mt-4 border-t pt-4 min-w-0">
      <h3>Lauf {index + 1}</h3>
      <p className="text-sm break-words">{run.model}</p>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 mt-3 text-sm">
        <div><dt>Eingabetoken</dt><dd>{run.input_tokens.toLocaleString('de-DE')}</dd></div>
        <div><dt>Ausgabetoken</dt><dd>{run.output_tokens.toLocaleString('de-DE')}</dd></div>
        <div><dt>Laufzeit</dt><dd>{Math.round(run.runtime_seconds).toLocaleString('de-DE')} s</dd></div>
        <div><dt>Geschätzte Kosten</dt><dd>{run.estimated_cost_usd === null ? 'kein Preis bekannt' : `${Number(run.estimated_cost_usd).toLocaleString('de-DE', {minimumFractionDigits:2, maximumFractionDigits:6})} USD`}</dd></div>
      </dl>
    </div>)}
  </section>;
}
