/**
 * What the screens say about how the processing of an original ended: the invoice screen (issue #500) and the screens of
 * the inbox (issue #501).
 *
 * The backend stores a stable reason code with every outcome that is not a success; this is the one map from such a
 * code to the German explanation: what happened, whether anything was sent, that the original is kept, and what the
 * user can do. An entry is one text where both places say the same, and an invoice text and an inbox text where the
 * words differ (what the document is called, where processing is started again, how to continue by hand). A code
 * without a text shows a general sentence, never the code. Statuses have their own short label for the status line.
 */

/** Where the user meets the document: the detail page of an invoice, or a document of the inbox. */
export type ProcessingContext = "invoice" | "inbox";

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
const SETTINGS = "in den Einstellungen unter „KI für Ihre Organisation“";
const CHOOSE_OTHER = `Bleibt der Fehler bestehen, kann Ihre Administration ${SETTINGS} ein anderes Modell wählen.`;

// ---- the invoice screen --------------------------------------------------------------------------------------
const BY_HAND = "die Angaben von Hand eintragen („Kostenposition erstellen“)";
const MANUAL = `Sie können ${BY_HAND}.`;
const NOT_SENT = "Das Dokument wurde nicht an einen KI-Anbieter übermittelt.";
// What the organization's own choices end can happen before the first request or while the invoice is being read (the
// consent is read again before every request): nothing more is sent, and what was already requested is on the usage card.
const NOTHING_MORE_SENT = "Es wird nichts (mehr) an einen KI-Anbieter übermittelt. Falls schon Anfragen gesendet wurden, steht ihr Verbrauch unter „Verbrauch der KI-Verarbeitung“.";
const PAID_AGAIN = "Ein erneuter Start liest das Dokument noch einmal ein und verursacht erneut Kosten.";
const LEGACY_OCR = `Die frühere Texterkennung konnte dieses Dokument nicht auswerten. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}.`;

// ---- the screens of the inbox --------------------------------------------------------------------------------
// The text of the inbox names the documents of the inbox and no other kind: which other kinds the one switch covers depends on the
// installation (invoices only where it processes them), which only the administration's settings answer tells.
// A document of the inbox is a verified range of pages of an original. It is processed from "Belege und Vorschläge",
// where it is started again and where its usage is shown; a cost position is created by hand under "Kostenpositionen".
const IN_BY_HAND = "die Kostenposition unter „Kostenpositionen“ von Hand anlegen";
const IN_MANUAL = `Sie können ${IN_BY_HAND}.`;
const IN_AGAIN = "unter „Belege und Vorschläge“ die Auswertung erneut anfordern";
const IN_NOT_SENT = "Die Seiten wurden nicht an einen KI-Anbieter übermittelt.";
const IN_USAGE_WHERE = "unter „Belege und Vorschläge“ im Abschnitt „Verbrauch der KI-Verarbeitung“";
const IN_USAGE = `Was bereits angefragt wurde, steht ${IN_USAGE_WHERE}.`;
const IN_NOTHING_MORE_SENT = `Es wird nichts (mehr) an einen KI-Anbieter übermittelt. Falls schon Anfragen gesendet wurden, steht ihr Verbrauch ${IN_USAGE_WHERE}.`;
const IN_PAID_AGAIN = "Eine erneute Auswertung liest die Seiten noch einmal ein und verursacht erneut Kosten.";
const IN_SMALLER = "Legen Sie unter „Belege und Vorschläge“ einen kleineren Seitenbereich an („Seitenbereich anlegen“), der nur diesen Beleg enthält.";
const IN_LEGACY_OCR = `Die frühere Texterkennung konnte diesen Beleg nicht auswerten. ${KEPT} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}.`;

type Entry = string | { invoice: string; inbox: string };

const PROCESSING_MESSAGES: Record<string, Entry> = {
  // The organization's choices and what the models need
  ai_import_disabled: {
    invoice: `Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet. ${NOTHING_MORE_SENT} ${KEPT} ${MANUAL} Ihre Administration kann ${SETTINGS} die KI einrichten und den Schalter für Teilungserklärungen und Rechnungen einschalten.`,
    inbox: `Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet. ${IN_NOTHING_MORE_SENT} ${KEPT} ${IN_MANUAL} Ihre Administration kann ${SETTINGS} die KI einrichten und den Schalter einschalten, der auch die Dokumente im Posteingang auswertet; danach können Sie ${IN_AGAIN}.`,
  },
  ai_invoice_confirmation_missing: `Ihre Organisation hat noch nicht bestätigt, dass Rechnungen an den KI-Anbieter übermittelt werden dürfen. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann das ${SETTINGS} bestätigen; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
  ai_inbox_confirmation_missing: `Ihre Organisation hat noch nicht bestätigt, dass Dokumente im Posteingang an den KI-Anbieter übermittelt werden dürfen. ${IN_NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann das ${SETTINGS} bestätigen, ohne den Schlüssel erneut einzugeben; danach können Sie ${IN_AGAIN}. ${IN_MANUAL}`,
  ai_document_model_missing: {
    invoice: `Diese Rechnung hat Seiten ohne Text (zum Beispiel einen Scan), und für das Einlesen fehlt ein Modell. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann ${SETTINGS} ein Modell unter „Dokumente einlesen“ wählen; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
    inbox: `Dieser Beleg hat Seiten ohne Text (zum Beispiel einen Scan oder ein Foto), und für das Einlesen fehlt ein Modell. ${IN_NOTHING_MORE_SENT} ${KEPT} Ihre Administration kann ${SETTINGS} ein Modell unter „Dokumente einlesen“ wählen; danach können Sie ${IN_AGAIN}. ${IN_MANUAL}`,
  },
  ai_key_unreadable: {
    invoice: `Der gespeicherte KI-Schlüssel Ihrer Organisation kann nicht gelesen werden. ${NOTHING_MORE_SENT} ${KEPT} Ihre Administration muss den Schlüssel ${SETTINGS} erneut hinterlegen; danach können Sie die Verarbeitung erneut starten.`,
    inbox: `Der gespeicherte KI-Schlüssel Ihrer Organisation kann nicht gelesen werden. ${IN_NOTHING_MORE_SENT} ${KEPT} Ihre Administration muss den Schlüssel ${SETTINGS} erneut hinterlegen; danach können Sie ${IN_AGAIN}. ${IN_MANUAL}`,
  },
  ai_settings_changed: {
    invoice: `Die KI-Einstellungen Ihrer Organisation (Anbieter, Schlüssel oder Modelle) wurden geändert, während diese Rechnung ausgelesen wurde. ${NOTHING_MORE_SENT} ${KEPT} Wenn Sie die Verarbeitung erneut starten, gelten die aktuellen Einstellungen. ${PAID_AGAIN} ${MANUAL}`,
    inbox: `Die KI-Einstellungen Ihrer Organisation (Anbieter, Schlüssel oder Modelle) wurden geändert, während dieser Beleg ausgelesen wurde. ${IN_NOTHING_MORE_SENT} ${KEPT} Wenn Sie ${IN_AGAIN}, gelten die aktuellen Einstellungen. ${IN_PAID_AGAIN} ${IN_MANUAL}`,
  },
  organization_ai_budget_exceeded: {
    invoice: `Das monatliche KI-Budget Ihrer Organisation ist ausgeschöpft; die nächste Anfrage an den Anbieter wurde deshalb nicht gesendet. ${KEPT} Was bis dahin verbraucht wurde, steht unter „Verbrauch der KI-Verarbeitung“. Ihre Administration kann das Limit ${SETTINGS} anpassen; danach, oder im nächsten Kalendermonat (UTC), können Sie die Verarbeitung erneut starten. ${MANUAL}`,
    inbox: `Das monatliche KI-Budget Ihrer Organisation ist ausgeschöpft; die nächste Anfrage an den Anbieter wurde deshalb nicht gesendet. ${KEPT} Was bis dahin verbraucht wurde, steht ${IN_USAGE_WHERE}. Ihre Administration kann das Limit ${SETTINGS} anpassen; danach, oder im nächsten Kalendermonat (UTC), können Sie ${IN_AGAIN}. ${IN_MANUAL}`,
  },
  // The bounds of one invoice, and of one document of the inbox
  invoice_page_limit: {
    invoice: `Das Dokument hat zu viele Seiten für die automatische Auswertung einer Rechnung. ${NOT_SENT} ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
    inbox: `Dieser Beleg hat zu viele Seiten für die automatische Auswertung. ${IN_NOT_SENT} ${KEPT} ${IN_SMALLER} ${IN_MANUAL}`,
  },
  invoice_text_limit: {
    invoice: `Das Dokument enthält mehr Text, als für eine Rechnung automatisch ausgewertet wird. Die Auswertung wurde beendet. ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
    inbox: `Dieser Beleg enthält mehr Text, als für einen Beleg automatisch ausgewertet wird. Die Auswertung wurde beendet. ${KEPT} ${IN_USAGE} ${IN_SMALLER} ${IN_MANUAL}`,
  },
  invoice_ai_limit: {
    invoice: `Die Auswertung hat die Grenze für eine einzelne Rechnung erreicht (Seiten, Token oder Kosten) und wurde beendet, damit keine weiteren Kosten entstehen. ${KEPT} ${MANUAL} Oder laden Sie eine kürzere Fassung hoch.`,
    inbox: `Die Auswertung hat die Grenze für einen einzelnen Beleg erreicht (Seiten, Token oder Kosten) und wurde beendet, damit keine weiteren Kosten entstehen. ${KEPT} ${IN_USAGE} ${IN_SMALLER} ${IN_MANUAL}`,
  },
  structured_selection_partial: `Dieser Beleg ist nur ein Teil einer E-Rechnung im PDF-Format. Deren Daten gelten für das gesamte Dokument und werden nicht für einzelne Seiten ausgewertet; es wurde nichts an einen KI-Anbieter übermittelt. ${KEPT} Wählen Sie unter „Belege und Vorschläge“ den Gesamtbeleg mit allen Seiten aus, oder ${IN_BY_HAND}.`,
  // The models and the provider
  model_reading_failed: {
    invoice: `Das Modell unter „Dokumente einlesen“ hat eine Seite nicht eingelesen (Fehler oder Zeitüberschreitung beim Anbieter, oder das Modell kann keine Bilder lesen). ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
    inbox: `Das Modell unter „Dokumente einlesen“ hat eine Seite nicht eingelesen (Fehler oder Zeitüberschreitung beim Anbieter, oder das Modell kann keine Bilder lesen). ${KEPT} ${IN_USAGE} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}. ${IN_PAID_AGAIN} ${CHOOSE_OTHER}`,
  },
  model_extraction_failed: {
    invoice: `Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert (Fehler oder Zeitüberschreitung beim Anbieter). ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
    inbox: `Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert (Fehler oder Zeitüberschreitung beim Anbieter). ${KEPT} ${IN_USAGE} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}. ${IN_PAID_AGAIN} ${CHOOSE_OTHER}`,
  },
  provider_unreachable: {
    invoice: `Der KI-Anbieter Ihrer Organisation konnte nicht erreicht werden (die Adresse ließ sich nicht auflösen oder ist nicht zulässig, oder die Verbindung kam nicht zustande). Die Anfrage wurde nicht gesendet und hat keine Kosten verursacht. Falls schon Anfragen gesendet wurden, steht ihr Verbrauch unter „Verbrauch der KI-Verarbeitung“. ${KEPT} Sie können die Verarbeitung erneut starten, sobald der Anbieter erreichbar ist, oder ${BY_HAND}. Bleibt der Fehler bestehen, kann Ihre Administration ${SETTINGS} die Adresse des Anbieters prüfen.`,
    inbox: `Der KI-Anbieter Ihrer Organisation konnte nicht erreicht werden (die Adresse ließ sich nicht auflösen oder ist nicht zulässig, oder die Verbindung kam nicht zustande). Die Anfrage wurde nicht gesendet und hat keine Kosten verursacht. Falls schon Anfragen gesendet wurden, steht ihr Verbrauch ${IN_USAGE_WHERE}. ${KEPT} Sie können ${IN_AGAIN}, sobald der Anbieter erreichbar ist, oder ${IN_BY_HAND}. Bleibt der Fehler bestehen, kann Ihre Administration ${SETTINGS} die Adresse des Anbieters prüfen.`,
  },
  model_result_invalid: {
    invoice: `Das Modell hat eine Antwort geliefert, die sich nicht als Rechnung auswerten ließ. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}. ${PAID_AGAIN} ${CHOOSE_OTHER}`,
    inbox: `Das Modell hat eine Antwort geliefert, die sich nicht als Beleg auswerten ließ. ${KEPT} ${IN_USAGE} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}. ${IN_PAID_AGAIN} ${CHOOSE_OTHER}`,
  },
  lease_lost: {
    invoice: `Die Verarbeitung wurde unterbrochen, bevor sie abgeschlossen war. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}. ${PAID_AGAIN}`,
    inbox: `Die Auswertung wurde unterbrochen, bevor sie abgeschlossen war. ${KEPT} ${IN_USAGE} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}. ${IN_PAID_AGAIN}`,
  },
  // Preparing the pages
  render_timeout: {
    invoice: `Das Aufbereiten einer Seite zum Einlesen hat zu lange gedauert. ${KEPT} Sie können die Verarbeitung erneut starten oder eine kleinere oder einfachere Datei hochladen. ${MANUAL}`,
    inbox: `Das Aufbereiten einer Seite zum Einlesen hat zu lange gedauert. ${KEPT} Sie können ${IN_AGAIN}; bleibt es dabei, reichen Sie eine kleinere oder einfachere Datei neu ein. ${IN_MANUAL}`,
  },
  render_unavailable: {
    invoice: `Eine Seite des Dokuments konnte nicht zum Einlesen aufbereitet werden. ${KEPT} Prüfen Sie, ob sich die PDF-Datei öffnen lässt, und laden Sie sie gegebenenfalls erneut hoch. Besteht das Problem weiter, wenden Sie sich an den Betreiber. ${MANUAL}`,
    inbox: `Eine Seite des Belegs konnte nicht zum Einlesen aufbereitet werden. ${KEPT} Prüfen Sie, ob sich die Datei öffnen lässt, und reichen Sie sie gegebenenfalls neu ein. Besteht das Problem weiter, wenden Sie sich an den Betreiber. ${IN_MANUAL}`,
  },
  image_pixel_limit: {
    invoice: `Eine Seite ist für das Einlesen zu groß. ${KEPT} Laden Sie eine Fassung mit kleineren Seiten hoch. ${MANUAL}`,
    inbox: `Eine Seite oder ein Bild ist für das Einlesen zu groß. ${KEPT} Reichen Sie eine Fassung mit kleineren Seiten oder einer geringeren Auflösung neu ein. ${IN_MANUAL}`,
  },
  // The original itself
  file_size_limit: {
    invoice: `Die Datei ist zu groß für die automatische Auswertung. ${KEPT} ${MANUAL}`,
    inbox: `Die Datei ist zu groß für die automatische Auswertung. ${KEPT} ${IN_MANUAL}`,
  },
  unsupported_format: {
    invoice: `Dieses Format wird nicht automatisch ausgewertet; unterstützt werden PDF-Dateien und E-Rechnungen im XML-Format. ${KEPT} ${MANUAL}`,
    inbox: `Dieses Format wird nicht automatisch ausgewertet; unterstützt werden PDF-Dateien, PNG- und JPEG-Bilder und E-Rechnungen im XML-Format. ${KEPT} ${IN_MANUAL}`,
  },
  invalid_document: `Die Datei ist beschädigt oder nicht lesbar und wurde nicht ausgewertet. ${KEPT} Reichen Sie eine korrigierte Fassung neu ein. ${IN_MANUAL}`,
  mime_mismatch: `Dateiinhalt und Dateityp stimmen nicht überein; die Datei wurde nicht ausgewertet. ${KEPT} Prüfen Sie die Datei und reichen Sie sie gegebenenfalls neu ein. ${IN_MANUAL}`,
  invalid_page_selection: `Der gewählte Seitenbereich ist für dieses Original nicht gültig. ${KEPT} Legen Sie unter „Belege und Vorschläge“ den Seitenbereich neu an. ${IN_MANUAL}`,
  source_page_count_changed: `Die Seitenzahl des Originals passt nicht mehr zur geprüften Aufteilung. ${KEPT} Wenden Sie sich an den Betreiber. ${IN_MANUAL}`,
  encrypted_pdf: `Das PDF ist passwortgeschützt und kann nicht ausgewertet werden. ${KEPT} Laden Sie eine Fassung ohne Passwortschutz hoch. ${MANUAL}`,
  pdf_page_limit: `Das PDF hat zu viele Seiten für eine Rechnung. ${KEPT} ${MANUAL} Oder laden Sie eine Fassung mit nur den Rechnungsseiten hoch.`,
  invalid_pdf: `Das PDF konnte nicht gelesen werden; die Datei ist möglicherweise beschädigt. ${KEPT} Prüfen Sie, ob sich die Datei öffnen lässt, und laden Sie sie gegebenenfalls erneut hoch. ${MANUAL}`,
  missing_fields: {
    invoice: "Die Auswertung hat nicht alle nötigen Angaben gefunden (mindestens Rechnungsdatum und Bruttobetrag). Prüfen Sie die vorgeschlagenen Felder und ergänzen Sie sie von Hand.",
    inbox: "Die Auswertung hat nicht alle nötigen Angaben gefunden (mindestens Rechnungsdatum und Bruttobetrag). Die vorgeschlagenen Felder sind ungeprüft; prüfen Sie sie am Original und ergänzen Sie sie unter „Rechnungsfelder prüfen“ von Hand.",
  },
  source_integrity_failed: "Die gespeicherte Datei stimmt nicht mit ihrer Prüfsumme überein; sie wurde deshalb nicht ausgewertet. Prüfen Sie die Integrität („Integrität prüfen“) und wenden Sie sich an den Betreiber.",
  storage_integrity_mismatch: `Das gespeicherte Original konnte nicht unverändert bestätigt werden; es wurde deshalb nicht ausgewertet. Wenden Sie sich an den Betreiber. ${IN_MANUAL}`,
  stored_original_unavailable: `Das gespeicherte Original ist derzeit nicht verfügbar; es wurde nichts ausgewertet. Wenden Sie sich an den Betreiber; danach können Sie ${IN_AGAIN}.`,
  // The installation and the processing itself
  pdf_parser_unavailable: {
    invoice: `Die Prüfung von PDF-Dateien ist auf diesem Server derzeit nicht verfügbar. ${KEPT} Wenden Sie sich an den Betreiber; danach können Sie die Verarbeitung erneut starten. ${MANUAL}`,
    inbox: `Die Prüfung von PDF-Dateien ist auf diesem Server derzeit nicht verfügbar. ${KEPT} Wenden Sie sich an den Betreiber; danach können Sie ${IN_AGAIN}. ${IN_MANUAL}`,
  },
  processing_disabled: `Die automatische Verarbeitung ist auf diesem Server abgeschaltet. ${KEPT} ${MANUAL}`,
  queue_full: `Die Warteschlange der automatischen Verarbeitung ist voll. ${KEPT} Sie können es später erneut starten oder ${BY_HAND}.`,
  extraction_timeout: {
    invoice: `Die Prüfung der Datei hat zu lange gedauert. ${KEPT} Sie können die Verarbeitung erneut starten oder ${BY_HAND}.`,
    inbox: `Die Prüfung der Datei hat zu lange gedauert. ${KEPT} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}.`,
  },
  processor_failed: {
    invoice: `Die Verarbeitung ist fehlgeschlagen. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}. Besteht das Problem weiter, wenden Sie sich an den Betreiber.`,
    inbox: `Die Auswertung ist fehlgeschlagen. ${KEPT} Sie können ${IN_AGAIN} oder ${IN_BY_HAND}. Besteht das Problem weiter, wenden Sie sich an den Betreiber.`,
  },
  worker_lease_expired: `Die Verarbeitung wurde unterbrochen, bevor sie abgeschlossen war. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}.`,
  retry_budget_exhausted: `Die Verarbeitung wurde dreimal versucht und nicht abgeschlossen; weitere Versuche sind nicht möglich. ${KEPT} ${MANUAL}`,
  attempt_limit: `Die Auswertung wurde dreimal versucht und nicht abgeschlossen; weitere Versuche sind nicht möglich. ${KEPT} ${IN_MANUAL}`,
  // The original is no longer the one in work, or its initiator may no longer work on it
  source_no_longer_editable: "Der Beleg wurde freigegeben oder durch eine neue Version ersetzt; die Verarbeitung wurde beendet. Das Original bleibt unverändert erhalten; es wurde nichts übernommen.",
  scope_permission_changed: `Die Verarbeitung wurde beendet, weil dem Benutzer, der sie ausgelöst hat, die Berechtigung dafür entzogen wurde. ${KEPT} Ein berechtigter Benutzer kann sie erneut starten.`,
  initiator_permission_changed: `Die Auswertung wurde beendet, weil dem Benutzer, der sie ausgelöst hat, die Berechtigung dafür entzogen wurde. ${KEPT} Ein berechtigter Benutzer kann sie unter „Belege und Vorschläge“ erneut anfordern.`,
  // Processed by the text recognition that earlier releases ran: stored results of earlier releases keep their reason
  ocr_unavailable: { invoice: LEGACY_OCR, inbox: IN_LEGACY_OCR },
  ocr_timeout: { invoice: LEGACY_OCR, inbox: IN_LEGACY_OCR },
  ocr_page_limit: { invoice: LEGACY_OCR, inbox: IN_LEGACY_OCR },
  text_size_limit: {
    invoice: LEGACY_OCR,
    inbox: `Der Text dieses Belegs ist zu umfangreich für die automatische Auswertung der E-Rechnungsdaten. ${KEPT} ${IN_MANUAL}`,
  },
  incomplete_text: { invoice: LEGACY_OCR, inbox: IN_LEGACY_OCR },
};

/**
 * The reasons for which the extraction of a document of the inbox waits for the organization and has not failed: the AI import
 * is off, the confirmation of the provider is missing, no reading model is chosen or the stored key cannot be read. The original
 * is kept and the extraction can be requested again once the administration has set it up.
 */
export const AWAITS_SETUP: ReadonlySet<string> = new Set(["ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable"]);

const text = (entry: Entry, context: ProcessingContext) => (typeof entry === "string" ? entry : entry[context]);

/** The explanation of every reason for one place, by code. */
function messagesFor(context: ProcessingContext): Record<string, string> {
  return Object.fromEntries(Object.entries(PROCESSING_MESSAGES).map(([code, entry]) => [code, text(entry, context)]));
}

export const INVOICE_PROCESSING_MESSAGES: Record<string, string> = messagesFor("invoice");
export const INBOX_PROCESSING_MESSAGES: Record<string, string> = messagesFor("inbox");
const MESSAGES: Record<ProcessingContext, Record<string, string>> = { invoice: INVOICE_PROCESSING_MESSAGES, inbox: INBOX_PROCESSING_MESSAGES };

const NO_EXPLANATION: Record<ProcessingContext, Set<string>> = {
  invoice: new Set(["legacy", "queued", "running", "succeeded"]),
  inbox: new Set(["queued", "running", "succeeded"]),
};

const GENERAL: Record<ProcessingContext, string> = {
  invoice: `Die Verarbeitung konnte nicht abgeschlossen werden. ${KEPT} Sie können sie erneut starten oder ${BY_HAND}.`,
  inbox: `Die Auswertung konnte nicht abgeschlossen werden. ${KEPT} ${IN_MANUAL}`,
};

/** The explanation for an outcome, or null while nothing has ended or went wrong (no text for nothing). */
export function invoiceProcessingMessage(status: string, code: string | null, context: ProcessingContext = "invoice"): string | null {
  if (NO_EXPLANATION[context].has(status)) return null;
  if (code && Object.hasOwn(MESSAGES[context], code)) return MESSAGES[context][code];
  return GENERAL[context];
}

/** Starting again was refused (the server answers 409): all three attempts are used, or the original is no longer the one in work. */
export const INVOICE_RETRY_REFUSED = `Die Verarbeitung kann nicht erneut gestartet werden: Entweder wurde sie schon dreimal versucht, oder der Beleg ist freigegeben oder ersetzt. ${MANUAL}`;
export const INVOICE_RETRY_FAILED = "Die Verarbeitung konnte nicht erneut gestartet werden. Bitte versuchen Sie es später noch einmal.";
