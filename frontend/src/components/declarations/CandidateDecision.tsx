import { useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { reviewDeclarationCandidate } from "../../api/endpoints";
import type { CandidateReviewResult, DeclarationCandidate, NonNullJsonValue } from "../../api/types";

interface CandidateDecisionProps {
  importId: string;
  candidate: DeclarationCandidate;
  draftRevision: number;
  onSaved: (result: CandidateReviewResult) => Promise<void> | void;
  onStale: () => Promise<void> | void;
}

function initialCorrection(candidate: DeclarationCandidate): string {
  return JSON.stringify(candidate.corrected_value_json ?? candidate.proposed_value_json ?? {}, null, 2);
}

function riskText(candidate: DeclarationCandidate): string {
  if (candidate.requires_individual_review) return "Einzelprüfung erforderlich";
  if (candidate.risk_class === "CRITICAL") return "Kritisches Risiko";
  if (candidate.risk_class === "MEDIUM") return "Mittleres Risiko";
  return "Niedriges Risiko";
}

export function CandidateDecision({ importId, candidate, draftRevision, onSaved, onStale }: CandidateDecisionProps) {
  const [correction, setCorrection] = useState(() => initialCorrection(candidate));
  const [comment, setComment] = useState(candidate.review_comment ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mutationInFlight = useRef(false);
  const reviewRequest = useRef<{ fingerprint: string; key: string } | null>(null);

  const submit = async (decision: "ACCEPTED" | "CORRECTED" | "REJECTED" | "NEEDS_CLARIFICATION") => {
    if (pending || mutationInFlight.current) return;
    setError(null);
    let correctedValue: NonNullJsonValue | undefined;
    if (decision === "CORRECTED") {
      try {
        const parsed: unknown = JSON.parse(correction);
        if (parsed === null || typeof parsed === "undefined") throw new Error();
        correctedValue = parsed as NonNullJsonValue;
      } catch {
        setError("Der korrigierte Wert muss gültiges JSON sein.");
        return;
      }
    }
    if (decision === "NEEDS_CLARIFICATION" && !comment.trim()) {
      setError("Bitte begründen Sie den Klärungsbedarf.");
      return;
    }

    mutationInFlight.current = true;
    setPending(true);
    try {
      const body = decision === "CORRECTED"
        ? { draft_revision: draftRevision, decision, corrected_value: correctedValue!, ...(comment.trim() ? { comment: comment.trim() } : {}) }
        : decision === "NEEDS_CLARIFICATION"
          ? { draft_revision: draftRevision, decision, comment: comment.trim() }
          : { draft_revision: draftRevision, decision, ...(comment.trim() ? { comment: comment.trim() } : {}) };
      const fingerprint = JSON.stringify([importId, candidate.id, body]);
      if (reviewRequest.current?.fingerprint !== fingerprint) {
        reviewRequest.current = { fingerprint, key: globalThis.crypto.randomUUID() };
      }
      const result = await reviewDeclarationCandidate(importId, candidate.id, body, reviewRequest.current.key);
      await onSaved(result);
      reviewRequest.current = null;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError("Der Entwurf wurde parallel geändert. Aktuelle Daten wurden neu geladen; Ihre Korrektur bleibt erhalten.");
        await onStale();
      } else {
        setError(caught instanceof Error ? caught.message : "Die Entscheidung konnte nicht gespeichert werden.");
      }
    } finally {
      mutationInFlight.current = false;
      setPending(false);
    }
  };

  return (
    <fieldset className="candidate-decision" disabled={pending}>
      <legend>Entscheidung</legend>
      <p className="declaration-risk-text"><strong>Risiko:</strong> {riskText(candidate)}</p>
      {candidate.requires_individual_review && <p>Dieser Vorschlag kann nicht gesammelt freigegeben werden.</p>}
      <label htmlFor={`candidate-correction-${candidate.id}`}>Korrigierter JSON-Wert</label>
      <textarea
        id={`candidate-correction-${candidate.id}`}
        value={correction}
        onChange={(event) => setCorrection(event.target.value)}
        rows={4}
        aria-describedby={`candidate-correction-help-${candidate.id}`}
      />
      <p id={`candidate-correction-help-${candidate.id}`} className="empty-hint">Strukturierte Werte wie MEA und Formeln werden als JSON gespeichert.</p>
      <label htmlFor={`candidate-comment-${candidate.id}`}>Kommentar</label>
      <textarea id={`candidate-comment-${candidate.id}`} value={comment} onChange={(event) => setComment(event.target.value)} rows={2} />
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="declaration-decision-actions">
        <button type="button" className="btn btn--primary" onClick={() => void submit("ACCEPTED")}>Akzeptieren</button>
        <button type="button" className="btn" onClick={() => void submit("CORRECTED")}>Korrektur speichern</button>
        <button type="button" className="btn" onClick={() => void submit("NEEDS_CLARIFICATION")}>Klärung anfordern</button>
        <button type="button" className="btn btn--danger" onClick={() => void submit("REJECTED")}>Ablehnen</button>
      </div>
    </fieldset>
  );
}
