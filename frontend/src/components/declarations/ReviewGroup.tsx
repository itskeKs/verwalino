import { useMemo, useState } from "react";
import type { CandidateReviewResult, DeclarationCandidate } from "../../api/types";
import { CandidateDecision } from "./CandidateDecision";
import { EvidenceViewer } from "./EvidenceViewer";

interface ReviewGroupProps {
  title: string;
  importId: string;
  candidates: DeclarationCandidate[];
  draftRevision: number;
  documentTitles: ReadonlyMap<string, string>;
  onSaved: (result: CandidateReviewResult) => Promise<void> | void;
  onStale: () => Promise<void> | void;
  onBulkAccept: (ids: string[]) => Promise<void> | void;
  bulkPending: boolean;
}

function isSafeForBulk(candidate: DeclarationCandidate): boolean {
  const confidence = Number(candidate.confidence);
  const hasBlockingIssue = candidate.issues.some((issue) => issue.severity === "WARNING" || issue.severity === "BLOCKER" || issue.code.includes("CONFLICT"));
  return candidate.review_status === "PENDING"
    && candidate.operation === "CREATE"
    && candidate.risk_class === "LOW"
    && confidence >= 0.85
    && !candidate.requires_individual_review
    && !hasBlockingIssue
    && !["RULE", "DOCUMENT_RELATION", "MEA_RESCALE"].includes(candidate.candidate_type);
}

function issuePrefix(severity: string): string {
  const labels: Record<string, string> = {
    INFO: "Hinweis",
    WARNING: "Prüfen",
    BLOCKER: "Freigabe gesperrt",
  };
  return labels[severity] ?? severity;
}

function riskText(candidate: DeclarationCandidate): string {
  if (candidate.requires_individual_review) return "Einzelprüfung erforderlich";
  if (candidate.risk_class === "CRITICAL") return "Kritisches Risiko";
  if (candidate.risk_class === "MEDIUM") return "Mittleres Risiko";
  return "Niedriges Risiko";
}

function renderStructuredRule(value: Record<string, unknown> | null) {
  if (!value) return null;
  const effect = value.effect as Record<string, unknown> | undefined;
  const validity = Object.fromEntries(
    Object.entries({ proposed_status: value.proposed_status, valid_from: value.valid_from, valid_to: value.valid_to })
      .filter(([, content]) => content !== undefined && content !== null),
  );
  const sections: Array<[string, unknown]> = [
    ["Geltungsbereich", value.scope],
    ["Wirkung", effect],
    ["Pflicht", effect?.obligation],
    ["Berechtigung", effect?.entitlement],
    ["Folge", effect?.consequence],
    ["Hinweis für die Verwaltung", effect?.administrative_note],
    ["Verteilungsformel", effect?.allocation_formula],
    ["Verantwortung", effect?.responsibility],
    ["Ausnahmen", effect?.exceptions],
    ["Gültigkeit", Object.keys(validity).length > 0 ? validity : undefined],
    ["Ablösungen", value.supersedes_subject_keys],
    ["Konflikte", value.conflicts_with_subject_keys],
    ["Zusammenfassung", value.summary ?? value.value],
  ];
  return (
    <dl className="declaration-rule-details">
      {sections.filter(([, content]) => content !== undefined && content !== null).map(([label, content]) => (
        <div key={label}><dt>{label}</dt><dd><pre>{JSON.stringify(content, null, 2)}</pre></dd></div>
      ))}
    </dl>
  );
}

export function ReviewGroup({ title, importId, candidates, draftRevision, documentTitles, onSaved, onStale, onBulkAccept, bulkPending }: ReviewGroupProps) {
  const safeIds = useMemo(() => candidates.filter(isSafeForBulk).map((candidate) => candidate.id), [candidates]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const selectedSafeIds = safeIds.filter((id) => selectedIds.has(id));

  const toggle = (id: string) => setSelectedIds((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <section className="declaration-review-group card" aria-labelledby={`review-group-${title}`}>
      <div className="declaration-review-group-heading">
        <h2 id={`review-group-${title}`}>{title}</h2>
        {safeIds.length > 0 && (
          <button
            type="button"
            className="btn"
            disabled={bulkPending}
            onClick={() => void onBulkAccept(selectedSafeIds.length > 0 ? selectedSafeIds : safeIds)}
          >
            Sichere Vorschläge akzeptieren ({selectedSafeIds.length || safeIds.length})
          </button>
        )}
      </div>
      <div className="declaration-candidate-list">
        {candidates.map((candidate) => {
          const selectable = isSafeForBulk(candidate);
          return (
            <article className="declaration-candidate" key={candidate.id}>
              <header>
                <div>
                  <h3>{candidate.field_key ?? candidate.subject_key}</h3>
                  <p aria-label={`Prüfstatus: ${candidate.review_status}`}><strong>Operation:</strong> {candidate.operation} · <strong>Confidence:</strong> {candidate.confidence} · <strong>Risiko:</strong> {riskText(candidate)} · <strong>Prüfstatus:</strong> {candidate.review_status}</p>
                </div>
                {selectable && (
                  <label className="declaration-bulk-select">
                    <input type="checkbox" checked={selectedIds.has(candidate.id)} onChange={() => toggle(candidate.id)} aria-label={`${candidate.field_key ?? candidate.subject_key} für Sammelfreigabe auswählen`} />
                    Für Sammelfreigabe auswählen
                  </label>
                )}
              </header>
              <div className="declaration-value-grid">
                <section><h4>Alter Wert</h4><pre>{JSON.stringify(candidate.previous_value_json, null, 2) ?? "—"}</pre></section>
                <section><h4>Vorschlag</h4><pre>{JSON.stringify(candidate.proposed_value_json, null, 2) ?? "—"}</pre></section>
              </div>
              <p><strong>Quellen: {candidate.evidence.length}</strong></p>
              {candidate.issues.length > 0 && <ul className="declaration-issues">{candidate.issues.map((issue) => <li key={issue.id}>{issuePrefix(issue.severity)}: {issue.message}</li>)}</ul>}
              {candidate.candidate_type === "RULE" && renderStructuredRule(candidate.proposed_value_json)}
              <EvidenceViewer importId={importId} evidence={candidate.evidence} documentTitles={documentTitles} />
              <CandidateDecision importId={importId} candidate={candidate} draftRevision={draftRevision} onSaved={onSaved} onStale={onStale} />
            </article>
          );
        })}
      </div>
    </section>
  );
}
