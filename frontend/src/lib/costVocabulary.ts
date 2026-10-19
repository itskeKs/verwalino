/**
 * Kosten-Vokabular – Mappings zwischen Frontend-UI-Werten und
 * Datenbank-Werten (Migration 0006).
 *
 * Die Datenbank verwendet deutsche Schlüssel (`UMLAGEFAEHIG`,
 * `WOHNFLAECHE` …). Die UI zeigt deutsche Labels, sendet aber
 * englische Kurzwerte ("full", "AREA", …). Diese Datei stellt die
 * bidirektionale Konvertierung bereit.
 */

// ======================================================================
// Tenant apportionability (Umlagefähigkeit)
// ======================================================================

export const APPORTIONABILITY_LABELS: Record<string, string> = {
  full: "Voll umlagefaehig",
  partial: "Teilweise umlagefaehig",
  none: "Nicht umlagefaehig",
  extern_pruefen: "Umlagefaehig extern pruefen",
  gemischt_pruefen: "Gemischt pruefen",
  manuell_pruefen: "Manuell pruefen",
};

/** Frontend-Wert → DB-Wert (was an die API gesendet wird). */
export function toDbApportionability(uiValue: string): string {
  switch (uiValue) {
    case "full":
      return "UMLAGEFAEHIG";
    case "partial":
      return "TEILWEISE_PRUEFEN";
    case "none":
      return "NICHT_UMLAGEFAEHIG";
    case "extern_pruefen":
      return "UMLAGEFAEHIG_EXTERN_PRUEFEN";
    case "gemischt_pruefen":
      return "GEMISCHT_PRUEFEN";
    case "manuell_pruefen":
      return "MANUELL_PRUEFEN";
    default:
      return uiValue;
  }
}

/** DB-Wert (von der API gelesen) → Frontend-Wert (UI). */
export function fromDbApportionability(dbValue: string): string {
  switch (dbValue) {
    case "UMLAGEFAEHIG":
      return "full";
    case "TEILWEISE_PRUEFEN":
      return "partial";
    case "NICHT_UMLAGEFAEHIG":
      return "none";
    case "UMLAGEFAEHIG_EXTERN_PRUEFEN":
      return "extern_pruefen";
    case "GEMISCHT_PRUEFEN":
      return "gemischt_pruefen";
    case "MANUELL_PRUEFEN":
      return "manuell_pruefen";
    default:
      return dbValue;
  }
}

// ======================================================================
// Distribution keys (Schlüssel)
// ======================================================================

export const DISTRIBUTION_KEY_LABELS: Record<string, string> = {
  "": "(kein)",
  MEA: "MEA",
  AREA: "Flaeche",
  HEATED_AREA: "Beheizte Flaeche",
  UNIT_COUNT: "Einheiten",
  PERSON_COUNT: "Personen",
  CONSUMPTION: "Verbrauch",
  EXTERNAL: "Extern",
  MANUAL: "Manuell",
};

/** Frontend-Wert → DB-Wert. */
export function toDbDistributionKey(uiValue: string): string {
  switch (uiValue) {
    case "AREA":
      return "WOHNFLAECHE";
    case "HEATED_AREA":
      return "BEHEIZTE_FLAECHE";
    case "UNIT_COUNT":
      return "EINHEIT";
    case "PERSON_COUNT":
      return "PERSONENZAHL";
    case "CONSUMPTION":
      return "VERBRAUCH";
    case "EXTERNAL":
      return "EXTERNE_ABRECHNUNG";
    case "MANUAL":
      return "MANUELLER_BETRAG";
    default:
      return uiValue;
  }
}

/** DB-Wert (von der API gelesen) → Frontend-Wert (UI). */
export function fromDbDistributionKey(dbValue: string): string {
  switch (dbValue) {
    case "WOHNFLAECHE":
      return "AREA";
    case "BEHEIZTE_FLAECHE":
      return "HEATED_AREA";
    case "EINHEIT":
      return "UNIT_COUNT";
    case "PERSONENZAHL":
      return "PERSON_COUNT";
    case "VERBRAUCH":
      return "CONSUMPTION";
    case "EXTERNE_ABRECHNUNG":
      return "EXTERNAL";
    case "MANUELLER_BETRAG":
      return "MANUAL";
    default:
      return dbValue;
  }
}
