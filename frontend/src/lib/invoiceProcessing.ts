/**
 * What the invoice screen says about how the processing of an original ended (issue #500).
 *
 * The backend stores a stable reason code with every outcome that is not a success; this is the one map from such a
 * code to the German explanation: what happened, that the original is kept, and what the user can do. A code without
 * a text shows a general sentence, never the code. Statuses have their own short label for the status line.
 */

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  legacy: "Noch nicht automatisch geprüft",
  queued: "Gespeichert · wartet auf Verarbeitung",
  running: "Rechnung wird ausgelesen",
  succeeded: "Felder vorbereitet · bitte prüfen",
  needs_review: "Manuelle Prüfung erforderlich",
  unsupported: "Format oder Umfang nicht unterstützt · Original prüfen",
  unavailable: "Automatische Auswertung nicht möglich · Original prüfen",
  timeout: "Zeitlimit erreicht · erneut versuchen oder manuell prüfen",
  failed: "Verarbeitung fehlgeschlagen · Original prüfen",
  canceled: "Verarbeitung beendet · Original prüfen",
};

const KEPT = "Das Original bleibt erhalten; es wurde nichts übernommen.";
const BY_HAND = "die Angaben von Hand eintragen („Kostenposition erstellen“)";
const MANUAL = `Sie können ${BY_HAND}.`;
const NOT_SENT = "Das Dokument wurde nicht an einen KI-Anbieter übermittelt.";
// What the organization's own choices end can happen before the first request or while the invoice is being read (the
// consent is read again before every request): nothing more is sent, and what was already requested is on the usage card.
const NOTHING_MORE_SENT = "Es wird nichts (mehr) an einen KI-Anbieter übermittelt. Falls schon Anfragen gesendet wurden, steht ihr Verbrauch unter „Verbrauch der KI-Verarbeitung“.";
const SETTINGS = "in den Einstellungen unter „KI für Ihre Organisation“";
const PAID_AGAIN = "Ein erneuter Start liest das Dokument noch einmal ein und verursacht erneut Kosten.";
const CHOOSE_OTHER = `Bleibt der Fehler bestehen, kann Ihre Administration ${SETTINGS} ein anderes Modell wählen.`;
const LEGACY_OCR = `Die frühere Texterkennung konnte dieses Dokument nicht auswerten. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}.`;

export const INVOICE_PROCESSING_MESSAGES: Record<string, string> = {
  // The organization's choices and what the models need
  ai_import_disabled: `Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet. ${NOTHING_MORE_SENT} ${KEPT} ${MANUAL} Ihre Administration kann ${SETTINGS} die KI einrichten und den Schalter für Teilungserklärungen und Rechnungen einschalten.`,
  ai_invoice_confirmation_missing: `Ihre Organisation hat noch nicht bestätigt, dass Rechnungen an den KI-Anbieter übermittelt werden dürfen. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann das ${SETTINGS} bestätigen; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
  ai_document_model_missing: `Diese Rechnung hat Seiten ohne Text (zum Beispiel einen Scan), und für das Einlesen fehlt ein Modell. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann ${SETTINGS} ein Modell unter „Dokumente einlesen“ wählen; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
  ai_key_unreadable: `Der gespeicherte KI-Schlüssel Ihrer Organisation kann nicht gelesen werden. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration muss den Schlüssel ${SETTINGS} erneut hinterlegen; danach können Sie die Verarbeitung erneut starten.`,
  ai_settings_changed: `Die KI-Einstellungen Ihrer Organisation (Anbieter, Schlüssel oder Modelle) wurden geändert, während diese Rechnung ausgelesen wurde. ${NOTHING_MORE_SENT} ${KEPT} Wenn Sie die Verarbeitung erneut starten, gelten die aktuellen Einstellungen. ${PAID_AGAIN} ${MANUAL}`,
  organization_ai_budget_exceeded: `Das monatliche KI-Budget Ihrer Organisation ist ausgeschöpft; die nächste Anfrage an den Anbieter wurde deshalb nicht gesendet. ${KEPT} Was bis dahin verbraucht wurde, steht unter „Verbrauch der KI-Verarbeitung“. Ihre Administration kann das Limit ${SETTINGS} anpassen; danach, oder im nächsten Kalendermonat (UTC), können Sie die Verarbeitung erneut starten. ${MANUAL}`,
  // The bounds of one invoice
  invoice_page_limit: `Das Dokument hat zu viele Seiten für die automatische Auswertung einer Rechnung. ${NOT_SENT} ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
  invoice_text_limit: `Das Dokument enthält mehr Text, als für eine Rechnung automatisch ausgewertet wird. Die Auswertung wurde beendet. ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
  invoice_ai_limit: `Die Auswertung hat die Grenze für eine einzelne Rechnung erreicht (Seiten, Token oder Kosten) und wurde beendet, damit keine weiteren Kosten entstehen. ${KEPT} ${MANUAL} Oder laden Sie eine kürzere Fassung hoch.`,
  // The models and the provider
  model_reading_failed: `Das Modell unter „Dokumente einlesen“ hat eine Seite nicht eingelesen (Fehler oder Zeitüberschreitung beim Anbieter, oder das Modell kann keine Bilder lesen). ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
  model_extraction_failed: `Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert (Fehler oder Zeitüberschreitung beim Anbieter). ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
  model_result_invalid: `Das Modell hat eine Antwort geliefert, die sich nicht als Rechnung auswerten ließ. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
  lease_lost: `Die Verarbeitung wurde unterbrochen, bevor sie abgeschlossen war. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}. ${PAID_AGAIN}`,
  // Preparing the pages
  render_timeout: `Das Aufbereiten einer Seite zum Einlesen hat zu lange gedauert. ${KEPT} Sie können die Verarbeitung erneut starten oder eine kleinere oder einfachere Datei hochladen. ${MANUAL}`,
  render_unavailable: `Eine Seite des Dokuments konnte nicht zum Einlesen aufbereitet werden. ${KEPT} Prüfen Sie, ob sich die PDF-Datei öffnen lässt, und laden Sie sie gegebenenfalls erneut hoch. Besteht das Problem weiter, wenden Sie sich an den Betreiber. ${MANUAL}`,
  image_pixel_limit: `Eine Seite ist für das Einlesen zu groß. ${KEPT} Laden Sie eine Fassung mit kleineren Seiten hoch. ${MANUAL}`,
  // The original itself
  file_size_limit: `Die Datei ist zu groß für die automatische Auswertung. ${KEPT} ${MANUAL}`,
  unsupported_format: `Dieses Format wird nicht automatisch ausgewertet; unterstützt werden PDF-Dateien und E-Rechnungen im XML-Format. ${KEPT} ${MANUAL}`,
  encrypted_pdf: `Das PDF ist passwortgeschützt und kann nicht ausgewertet werden. ${KEPT} Laden Sie eine Fassung ohne Passwortschutz hoch. ${MANUAL}`,
  pdf_page_limit: `Das PDF hat zu viele Seiten für eine Rechnung. ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
  invalid_pdf: `Das PDF konnte nicht gelesen werden; die Datei ist möglicherweise beschädigt. ${KEPT} Prüfen Sie, ob sich die Datei öffnen lässt, und laden Sie sie gegebenenfalls erneut hoch. ${MANUAL}`,
  missing_fields: "Die Auswertung hat nicht alle nötigen Angaben gefunden (mindestens Rechnungsdatum und Bruttobetrag). Prüfen Sie die vorgeschlagenen Felder und ergänzen Sie sie von Hand.",
  source_integrity_failed: "Die gespeicherte Datei stimmt nicht mit ihrer Prüfsumme überein; sie wurde deshalb nicht ausgewertet. Prüfen Sie die Integrität („Integrität prüfen“) und wenden Sie sich an den Betreiber.",
  // The installation and the processing itself
  pdf_parser_unavailable: `Die Prüfung von PDF-Dateien ist auf diesem Server derzeit nicht verfügbar. ${KEPT} Wenden Sie sich an den Betreiber; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
  processing_disabled: `Die automatische Verarbeitung ist auf diesem Server abgeschaltet. ${KEPT} ${MANUAL}`,
  queue_full: `Die Warteschlange der automatischen Verarbeitung ist voll. ${KEPT} Sie können es später erneut starten oder ${BY_HAND}.`,
  extraction_timeout: `Die Prüfung der Datei hat zu lange gedauert. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}.`,
  processor_failed: `Die Verarbeitung ist fehlgeschlagen. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}. Besteht das Problem weiter, wenden Sie sich an den Betreiber.`,
  worker_lease_expired: `Die Verarbeitung wurde unterbrochen, bevor sie abgeschlossen war. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}.`,
  retry_budget_exhausted: `Die Verarbeitung wurde dreimal versucht und nicht abgeschlossen; weitere Versuche sind nicht möglich. ${KEPT} ${MANUAL}`,
  // The original is no longer the one in work, or its initiator may no longer work on it
  source_no_longer_editable: "Der Beleg wurde freigegeben oder durch eine neue Version ersetzt; die Verarbeitung wurde beendet. Das Original bleibt unverändert erhalten; es wurde nichts übernommen.",
  scope_permission_changed: `Die Verarbeitung wurde beendet, weil dem Benutzer, der sie ausgelöst hat, die Berechtigung dafür entzogen wurde. ${KEPT} Ein berechtigter Benutzer kann sie erneut starten.`,
  // Processed by the text recognition that earlier releases ran
  ocr_unavailable: LEGACY_OCR,
  ocr_timeout: LEGACY_OCR,
  ocr_page_limit: LEGACY_OCR,
  text_size_limit: LEGACY_OCR,
  incomplete_text: LEGACY_OCR,
};

const NO_EXPLANATION = new Set(["legacy", "queued", "running", "succeeded"]);

/** The explanation for an outcome, or null while nothing has ended or went wrong (no text for nothing). */
export function invoiceProcessingMessage(status: string, code: string | null): string | null {
  if (NO_EXPLANATION.has(status)) return null;
  if (code && Object.hasOwn(INVOICE_PROCESSING_MESSAGES, code)) return INVOICE_PROCESSING_MESSAGES[code];
  return `Die Verarbeitung konnte nicht abgeschlossen werden. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}.`;
}

/** Starting again was refused (the server answers 409): all three attempts are used, or the original is no longer the one in work. */
export const INVOICE_RETRY_REFUSED = `Die Verarbeitung kann nicht erneut gestartet werden: Entweder wurde sie schon dreimal versucht, oder der Beleg ist freigegeben oder ersetzt. ${MANUAL}`;
export const INVOICE_RETRY_FAILED = "Die Verarbeitung konnte nicht erneut gestartet werden. Bitte versuchen Sie es später noch einmal.";
