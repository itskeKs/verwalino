/**
 * The words for how far the files of a Posteingang batch have come: the batch page (`IntakeBatchPage`) and the row of the
 * batch in „Meldungen“ (`MeldungenPage`) both read this table and nowhere else, so that a stage is called the same on both.
 * A test (`backend/tests/test_meldungen_intake.py`) fails when a status or stage that the database names has no word here.
 */
import type { IntakeStatus } from '../api/intake';

/** What a file of a batch is: the group the batch page counts it in (`status` of `intake_batch_status`). */
export const INTAKE_STATUS_LABELS: Record<IntakeStatus, string> = {
  waiting: 'Datei fehlt',
  received: 'Empfangen',
  processing: 'In Verarbeitung',
  review: 'Zur Sichtung',
  confirmed: 'Bestätigt',
  failed: 'Gesperrt / fehlgeschlagen',
};

/** Where a file of a batch is in its processing (`stage` of `intake_batch_status`), in the order of the pipeline. */
export type IntakeStage = 'inspection' | 'preparation' | 'extraction' | 'split' | 'review';

export const INTAKE_STAGE_LABELS: Record<IntakeStage, string> = {
  inspection: 'Dateiprüfung',
  preparation: 'Dokumentvorbereitung',
  extraction: 'Auswertung',
  split: 'Aufteilung ausstehend',
  review: 'Vorschläge verfügbar',
};
