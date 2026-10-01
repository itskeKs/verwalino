import type { InvoiceDocument } from "../api/types";

const labels: Record<string, string> = {
  legacy: "Noch nicht automatisch geprüft",
  queued: "Gespeichert · wartet auf Verarbeitung",
  running: "Rechnung wird ausgelesen",
  succeeded: "Felder vorbereitet · bitte prüfen",
  needs_review: "Manuelle Prüfung erforderlich",
  unsupported: "Format oder Umfang nicht unterstützt · Original prüfen",
  unavailable: "Texterkennung nicht verfügbar · erneut versuchen oder manuell prüfen",
  timeout: "Zeitlimit erreicht · erneut versuchen oder manuell prüfen",
  failed: "Verarbeitung fehlgeschlagen · Original prüfen",
  canceled: "Verarbeitung beendet · Beleg bereits freigegeben oder ersetzt",
};

export function InvoiceProcessingStatus({ document }: { document: InvoiceDocument }) {
  return <span role="status">{labels[document.processing_status] ?? labels.legacy}</span>;
}
