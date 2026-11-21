import { useEffect, useState } from "react";
import { getInvoiceModelUsage } from "../api/endpoints";
import type { InvoiceModelUsage as Usage } from "../api/types";
import { Block, UsageRows } from "./declarations/DeclarationRunBudget";

const STAGE_TITLES = { READING: "Dokumente einlesen", EXTRACTION: "Informationen extrahieren" } as const;

/**
 * What the model-assisted processing of one invoice has cost so far, over all its attempts (also failed and ended
 * ones), per stage and in total. It reads the same ledger the monthly limit counts. Usage the provider did not
 * report is shown as the reservation it is, a model without a price as having none: never as 0,00 USD.
 * Nothing is shown while no request has been made.
 */
export function InvoiceModelUsage({ documentId, processingStatus }: { documentId: string; processingStatus: string }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);
  // A run ends with a new status: the amounts are read again then.
  useEffect(() => {
    let current = true;
    getInvoiceModelUsage(documentId)
      .then(value => { if (current) { setUsage(value); setFailed(false); } })
      .catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [documentId, processingStatus]);

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
