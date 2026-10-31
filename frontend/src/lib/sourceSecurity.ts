import type { AccessPreparation } from "../api/types";
import type { BadgeTone } from "../components/ui/badge";

export type SourceSecurityMode = AccessPreparation["mode"];
export interface SourceSecurityStatus { mode: SourceSecurityMode; changed_at: string | null }

export const sourceSecurityLabels: Record<SourceSecurityMode, string> = {
  PREPARATION: "Vorbereitung", ENFORCED: "Zugriffsregeln aktiv", PAUSED: "Aktionen pausiert",
};
export const sourceSecurityGuidance: Record<SourceSecurityMode, { tone: BadgeTone; meaning: string; next: string; to: string }> = {
  PREPARATION: {
    tone: "warning",
    meaning: "Der objektbezogene Quellschutz ist noch aus. Mitarbeitende können bisher unzugeordnete Bestände weiterhin sehen. Bereits zugeordnete Finanzbereiche benötigen ihre ausdrücklichen Teamrechte.",
    next: "Prüfen Sie zuerst Teamrechte und Bestand. Bestätigen Sie anschließend Budgetregel, Prüfstand und Altbestandsentscheidung, bevor Sie die Zugriffsregeln aktivieren.",
    to: "/berechtigungen/bestand",
  },
  ENFORCED: {
    tone: "success",
    meaning: "Mitarbeitende sehen nur ausdrücklich freigegebene Objektdaten, Quellen und Finanzbereiche. Gebäudedaten-, Kontaktdaten- und Finanzrechte werden unabhängig geprüft. Die Adminrolle ersetzt keine Quellenfreigabe.",
    next: "Prüfen Sie offene Freigaben und Quarantänen. Bei einer Störung können Sie die Aktionen unter Bestand prüfen pausieren; die eingeschränkten Leserechte bleiben bestehen.",
    to: "/berechtigungen/pruefungen",
  },
  PAUSED: {
    tone: "warning",
    meaning: "Quellen- und Finanzaktionen sind angehalten. Mitarbeitende behalten nur ihre ausdrücklich freigegebenen Leserechte. Die Pause stellt keinen früheren breiten Zugriff wieder her.",
    next: "Prüfen Sie vor der Wiederaufnahme einen neuen Prüfstand nach der letzten Pause und bestätigen Sie die aktuelle Altbestandsentscheidung unter Bestand prüfen.",
    to: "/berechtigungen/bestand",
  },
};
