import type { ReactNode } from 'react';
import type { DeclarationRunBudget as Run, DeclarationRunStage } from '../../api/types';

type Usage = Pick<Run, 'usage_status' | 'reported_input_tokens' | 'reported_output_tokens' | 'input_tokens' | 'output_tokens' | 'estimated_cost_usd'>;

const STAGE_TITLES: Record<DeclarationRunStage['stage'], string> = {
  READING: 'Dokumente einlesen',
  EXTRACTION: 'Informationen extrahieren',
};

const number = (value: number | null | undefined) => value?.toLocaleString('de-DE');

// The usage vocabulary is shared by the stages and the total of a run: reported, reserved, both, or unknown.
function UsageRows({ usage, runtime }: { usage: Usage; runtime?: number }) {
  const status = usage.usage_status;
  return <>
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 mt-3 text-sm">
      {status === 'UNKNOWN' ? <div><dt>Tokenverbrauch</dt><dd>Tokenverbrauch unbekannt</dd></div> : <>
        {(status === 'REPORTED' || status === 'PARTIAL') && <>
          <div><dt>{status === 'PARTIAL' ? 'Gemeldete Eingabetoken' : 'Eingabetoken'}</dt><dd>{number(usage.reported_input_tokens)}</dd></div>
          <div><dt>{status === 'PARTIAL' ? 'Gemeldete Ausgabetoken' : 'Ausgabetoken'}</dt><dd>{number(usage.reported_output_tokens)}</dd></div>
        </>}
        {(status === 'RESERVED' || status === 'PARTIAL') && <>
          <div><dt>{status === 'PARTIAL' ? 'Konservativ erfasste Eingabetoken' : 'Reservierte Eingabetoken'}</dt><dd>{number(usage.input_tokens)}</dd></div>
          <div><dt>{status === 'PARTIAL' ? 'Konservativ erfasste Ausgabetoken' : 'Reservierte Ausgabetoken'}</dt><dd>{number(usage.output_tokens)}</dd></div>
        </>}
      </>}
      {runtime !== undefined && <div><dt>Laufzeit</dt><dd>{Math.round(runtime).toLocaleString('de-DE')} s</dd></div>}
      <div><dt>{status === 'REPORTED' ? 'Geschätzte Kosten' : 'Konservative Kostenschätzung'}</dt><dd>{usage.estimated_cost_usd === null ? 'kein Preis bekannt' : `${Number(usage.estimated_cost_usd).toLocaleString('de-DE', {minimumFractionDigits:2, maximumFractionDigits:6})} USD`}</dd></div>
    </dl>
    {status === 'RESERVED' && <p className="text-sm text-muted-foreground mt-3">Der tatsächliche Tokenverbrauch ist noch nicht bekannt. Die Reservierung bleibt bis zur Klärung vorsorglich im Budget erfasst.</p>}
    {status === 'PARTIAL' && <p className="text-sm text-muted-foreground mt-3">Anbieterangaben liegen nur teilweise vor. Die konservativ erfassten Werte enthalten auch den weiterhin reservierten Anteil.</p>}
  </>;
}

function Block({ id, title, model, children }: { id: string; title: string; model?: string; children: ReactNode }) {
  return <div role="group" aria-labelledby={id} className="mt-3 min-w-0">
    <h4 id={id} className="text-sm font-semibold">{title}</h4>
    {model && <p className="text-sm break-words">{model}</p>}
    {children}
  </div>;
}

export function DeclarationRunBudget({ runs }: { runs: Run[] }) {
  if (!runs.length) return null;
  return <section className="card p-5 sm:p-6 my-5 min-w-0" aria-labelledby="declaration-run-budget-heading">
    <h2 id="declaration-run-budget-heading">Verbrauch je Importlauf</h2>
    <p className="text-sm text-muted-foreground mt-2">Anbieterangaben und vorsorglich reservierte Token werden getrennt ausgewiesen. Das Einlesen der Seiten und das Extrahieren der Informationen sind je Lauf getrennt aufgeführt. Die Laufzeit stammt aus dem jeweiligen Lauf. Bekannte Kosten sind Schätzungen in USD; die Abrechnung erfolgt beim Anbieter.</p>
    {runs.map((run, index) => <div key={run.id} className="mt-4 border-t pt-4 min-w-0">
      <h3>Lauf {index + 1}</h3>
      {run.stages.length === 0 && <p className="text-sm break-words">{run.model}</p>}
      {run.stages.map(stage => <Block key={stage.stage} id={`${run.id}-${stage.stage}`} title={STAGE_TITLES[stage.stage]} model={stage.model}>
        <UsageRows usage={stage} />
      </Block>)}
      {run.stages.length > 0
        ? <Block id={`${run.id}-total`} title="Gesamt"><UsageRows usage={run} runtime={run.runtime_seconds} /></Block>
        : <UsageRows usage={run} runtime={run.runtime_seconds} />}
    </div>)}
  </section>;
}
