import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  approveDeclarationImport,
  bulkReviewDeclarationCandidates,
  getDeclarationApprovalPreview,
  listDeclarationCandidates,
  listDeclarationDocuments,
} from "../api/endpoints";
import type { ApprovalPreview, DeclarationCandidate, DeclarationDocument } from "../api/types";
import { ApprovalSummary } from "../components/declarations/ApprovalSummary";
import { ReviewGroup } from "../components/declarations/ReviewGroup";

const GROUPS = [
  ["Stammdaten", "master"],
  ["Einheiten", "units"],
  ["Zuordnungen", "assignments"],
  ["Regeln", "rules"],
  ["Klärungsbedarf", "clarifications"],
] as const;

type GroupKey = typeof GROUPS[number][1];

function groupFor(candidate: DeclarationCandidate): GroupKey {
  if (candidate.candidate_type === "RULE") return "rules";
  if (candidate.issues.some((issue) => issue.resolution_status === "OPEN" && (issue.severity === "WARNING" || issue.severity === "BLOCKER" || issue.code.includes("CONFLICT")))) return "clarifications";
  if (candidate.candidate_type === "ASSIGNMENT" || candidate.candidate_type === "DOCUMENT_RELATION") return "assignments";
  if (candidate.candidate_type === "UNIT_CREATE" || candidate.candidate_type === "UNIT_FIELD" || candidate.candidate_type === "MEA_RESCALE") return "units";
  return "master";
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : "Die Review-Daten konnten nicht geladen werden.";
}

export function DeclarationReviewPage() {
  const { importId } = useParams<{ importId: string }>();

  return <DeclarationReviewImportPage key={importId ?? "missing-import"} importId={importId} />;
}

function DeclarationReviewImportPage({ importId }: { importId: string | undefined }) {
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState<DeclarationCandidate[]>([]);
  const [documents, setDocuments] = useState<DeclarationDocument[]>([]);
  const [preview, setPreview] = useState<ApprovalPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bulkPending, setBulkPending] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [approvalPending, setApprovalPending] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const reloadGeneration = useRef(0);
  const hasLoaded = useRef(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const approvalTriggerRef = useRef<HTMLButtonElement>(null);
  const openingConfirmation = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const approvalInFlight = useRef(false);

  const reload = useCallback(async () => {
    if (!importId) return;
    const generation = ++reloadGeneration.current;
    if (!hasLoaded.current) setLoading(true);
    if (generation === reloadGeneration.current) setError(null);
    try {
      const [loadedCandidates, loadedDocuments, loadedPreview] = await Promise.all([
        listDeclarationCandidates(importId),
        listDeclarationDocuments(importId),
        getDeclarationApprovalPreview(importId),
      ]);
      if (generation !== reloadGeneration.current) return;
      setCandidates(loadedCandidates);
      setDocuments(loadedDocuments);
      setPreview(loadedPreview);
      hasLoaded.current = true;
    } catch (caught) {
      if (generation === reloadGeneration.current) setError(readableError(caught));
    } finally {
      if (generation === reloadGeneration.current) setLoading(false);
    }
  }, [importId]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    if (!showConfirmation) return;
    const dialog = dialogRef.current;
    const trigger = approvalTriggerRef.current;
    if (!dialog) return;
    try {
      if (!dialog.open) dialog.showModal();
    } catch {
      dialog.setAttribute("open", "");
    }
    return () => {
      if (dialog.open) dialog.close?.();
      trigger?.focus();
    };
  }, [showConfirmation]);

  const documentTitles = useMemo(() => new Map(documents.map((document) => [document.id, document.title])), [documents]);
  const grouped = useMemo(() => {
    const initial: Record<GroupKey, DeclarationCandidate[]> = { master: [], units: [], assignments: [], rules: [], clarifications: [] };
    for (const candidate of candidates) initial[groupFor(candidate)].push(candidate);
    return initial;
  }, [candidates]);

  const handleSaved = useCallback(async () => {
    await reload();
  }, [reload]);

  const handleBulkAccept = useCallback(async (ids: string[]) => {
    if (!importId || !preview || ids.length === 0 || bulkPending) return;
    setBulkPending(true);
    setError(null);
    try {
      await bulkReviewDeclarationCandidates(importId, { draft_revision: preview.draft_revision, candidate_ids: ids, decision: "ACCEPTED" });
      await reload();
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBulkPending(false);
    }
  }, [bulkPending, importId, preview, reload]);

  const openConfirmation = () => {
    if (!preview?.can_approve || showConfirmation || openingConfirmation.current) return;
    openingConfirmation.current = true;
    setApprovalError(null);
    const key = globalThis.crypto.randomUUID();
    idempotencyKeyRef.current = key;
    setShowConfirmation(true);
  };

  const closeConfirmation = () => {
    if (approvalPending) return;
    openingConfirmation.current = false;
    idempotencyKeyRef.current = null;
    setShowConfirmation(false);
    setApprovalError(null);
  };

  const applyApproval = async () => {
    const key = idempotencyKeyRef.current;
    if (!importId || !preview?.can_approve || !key || approvalPending || approvalInFlight.current) return;
    approvalInFlight.current = true;
    setApprovalPending(true);
    setApprovalError(null);
    try {
      const result = await approveDeclarationImport(importId, {
        draft_revision: preview.draft_revision,
        idempotency_key: key,
      });
      openingConfirmation.current = false;
      idempotencyKeyRef.current = null;
      setShowConfirmation(false);
      navigate(`/objekte/${result.property_id}`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) await reload();
      setApprovalError(readableError(caught));
    } finally {
      approvalInFlight.current = false;
      setApprovalPending(false);
    }
  };

  if (!importId) return <main className="declaration-review-page"><p className="error-text">Die Import-ID fehlt.</p></main>;

  return (
    <main className="declaration-review-page">
      <div className="declaration-review-heading">
        <div>
          <h1>Teilungserklärung prüfen</h1>
          <p className="empty-hint">Jede Übernahme bleibt mit ihrer Quelle verbunden. Für risikoreiche Vorschläge ist eine Einzelprüfung erforderlich.</p>
        </div>
        {preview && <span className="badge badge--review">Entwurfsstand {preview.draft_revision}</span>}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      {loading ? <p aria-live="polite">Review-Daten werden geladen.</p> : (
        <>
          {GROUPS.map(([title, key]) => grouped[key].length > 0 && preview && (
            <ReviewGroup
              key={key}
              title={title}
              importId={importId}
              candidates={grouped[key]}
              draftRevision={preview.draft_revision}
              documentTitles={documentTitles}
              onSaved={handleSaved}
              onStale={reload}
              onBulkAccept={handleBulkAccept}
              bulkPending={bulkPending}
            />
          ))}
          {candidates.length === 0 && <p className="empty-hint">Es liegen keine Vorschläge zur Prüfung vor.</p>}
          <section className="card declaration-approval-panel" aria-labelledby="declaration-approval-heading">
            <h2 id="declaration-approval-heading">Übernahme</h2>
            {preview && !preview.can_approve && <p>Die Freigabe ist noch nicht möglich: noch {preview.pending_candidate_ids.length} offene Vorschläge oder gesperrte Hinweise.</p>}
            <button ref={approvalTriggerRef} type="button" className="btn btn--primary" disabled={!preview?.can_approve} onClick={openConfirmation}>Übernahme vorbereiten</button>
          </section>
        </>
      )}
      {showConfirmation && preview && (
        <ApprovalSummary
          preview={preview}
          candidates={candidates}
          documents={documents}
          submitting={approvalPending}
          onConfirm={() => void applyApproval()}
          onClose={closeConfirmation}
          error={approvalError}
          dialogRef={dialogRef}
        />
      )}
    </main>
  );
}
