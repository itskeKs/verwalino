import { type RefObject, type ReactNode } from "react";
import type { ApprovalPreview, DeclarationCandidate, DeclarationDocument } from "../../api/types";

interface ApprovalSummaryProps {
  preview: ApprovalPreview;
  candidates: DeclarationCandidate[];
  documents: DeclarationDocument[];
  submitting: boolean;
  onConfirm: () => void;
  onClose: () => void;
  error: string | null;
  dialogRef: RefObject<HTMLDialogElement | null>;
  children?: ReactNode;
  confirmationBlocked?: boolean;
}

function issuePrefix(severity: string): string {
  return { INFO: "Hinweis", WARNING: "Prüfen", BLOCKER: "Freigabe gesperrt" }[severity] ?? severity;
}

export function ApprovalSummary({ preview, candidates, documents, submitting, onConfirm, onClose, error, dialogRef, children, confirmationBlocked }: ApprovalSummaryProps) {
  const effective = candidates.filter((candidate) => candidate.review_status === "ACCEPTED" || candidate.review_status === "CORRECTED");
  const creates = effective.filter((candidate) => candidate.operation === "CREATE").length;
  const updates = effective.filter((candidate) => candidate.operation === "UPDATE").length;
  const unchanged = effective.filter((candidate) => candidate.operation === "KEEP").length;
  const rejected = candidates.filter((candidate) => candidate.review_status === "REJECTED").length;
  const meaEffects = effective.filter((candidate) => candidate.candidate_type === "MEA_RESCALE").length;
  const unresolved = preview.pending_candidate_ids.length + preview.issues.filter((issue) => issue.severity === "BLOCKER" || issue.resolution_status !== "RESOLVED").length;

  return (
    <dialog
      ref={dialogRef}
      className="declaration-approval-dialog"
      aria-labelledby="declaration-approval-title"
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id="declaration-approval-title">Übernahme bestätigen</h2>
      <p>Die Übernahme schreibt erst nach dieser Bestätigung in die kanonischen Objekt-Daten.</p>
      <dl className="declaration-approval-summary">
        <div><dt>Neue Einträge</dt><dd>{creates}</dd></div>
        <div><dt>Aktualisierungen</dt><dd>{updates}</dd></div>
        <div><dt>Unveränderte Werte</dt><dd>{unchanged}</dd></div>
        <div><dt>Abgelehnte Vorschläge</dt><dd>{rejected}</dd></div>
        <div><dt>Offene Punkte</dt><dd>{unresolved}</dd></div>
        <div><dt>Dokumente</dt><dd>{documents.length}</dd></div>
        <div><dt>MEA-Skalierungseffekte</dt><dd>{meaEffects}</dd></div>
      </dl>
      {preview.issues.length > 0 && <ul className="declaration-issues">{preview.issues.map((issue) => <li key={issue.id}>{issuePrefix(issue.severity)}: {issue.message}</li>)}</ul>}
      {children}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="button" className="btn" autoFocus disabled={submitting} onClick={onClose}>Abbrechen</button>
        <button type="button" className="btn btn--primary" disabled={submitting || !preview.can_approve || confirmationBlocked} onClick={onConfirm}>Endgültig übernehmen</button>
      </div>
    </dialog>
  );
}
