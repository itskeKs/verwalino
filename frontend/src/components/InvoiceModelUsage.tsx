import { useCallback, useEffect, useState } from "react";
import { getInvoiceModelUsage } from "../api/endpoints";
import type { InvoiceModelUsage as Usage } from "../api/types";
import { Block, UsageRows } from "./declarations/DeclarationRunBudget";

const STAGE_TITLES = { READING: "Dokumente einlesen", EXTRACTION: "Informationen extrahieren" } as const;

/**
 * What the model-assisted processing of one document has cost so far, over all its attempts (also failed and ended
 * ones), per stage and in total: an invoice (issue #500) or a document of the inbox (issue #501), whose ``load`` reads
 * the same ledger the monthly limit counts. Usage the provider did not report is shown as the reservation it is, a model
 * without a price as having none: never as 0,00 USD. Nothing is shown while no request has been made. The amounts are
 * read again when ``reloadKey`` changes, which is when a run ends with a new state.
 */
export function ModelUsage({ load, reloadKey }: { load: () => Promise<Usage>; reloadKey: string }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let current = true;
    load()
      .then(value => { if (current) { setUsage(value); setFailed(false); } })
      .catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [load, reloadKey]);

  if (failed) return <p role="alert" className="error-text my-4">Der Verbrauch der KI-Verarbeitung konnte nicht geladen werden.</p>;
  if (!usage?.total) return null;
  return <section className="card p-5 sm:p-6 my-5 min-w-0" aria-labelledby="invoice-model-usage-heading">
    <h2 id="invoice-model-usage-heading">Verbrauch der KI-Verarbeitung</h2>
    <p className="text-sm text-muted-foreground mt-2">Enthalten sind alle Versuche dieses Belegs, auch fehlgeschlagene und beendete. Anbieterangaben und vorsorglich reservierte Token werden getrennt ausgewiesen; die Reservierungen zählen zum monatlichen Limit. Bekannte Kosten sind Schätzungen in USD; die Abrechnung erfolgt beim Anbieter.</p>
    {usage.stages.map((stage, index) => <Block key={index} id={`invoice-usage-${index}`} title={STAGE_TITLES[stage.stage]} model={stage.model}>
      <p className="text-sm">{stage.calls === 1 ? "1 Aufruf" : `${stage.calls} Aufrufe`}</p>
      <UsageRows usage={stage} />
    </Block>)}
    <Block id="invoice-usage-total" title="Gesamt"><UsageRows usage={usage.total} /></Block>
  </section>;
}

/** The usage card of an invoice. */
export function InvoiceModelUsage({ documentId, processingStatus }: { documentId: string; processingStatus: string }) {
  const load = useCallback(() => getInvoiceModelUsage(documentId), [documentId]);
  return <ModelUsage load={load} reloadKey={processingStatus} />;
}
