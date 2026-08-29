import { useState } from "react";
import { declarationImportDocumentContentUrl } from "../../api/endpoints";
import type { CandidateEvidence } from "../../api/types";

interface EvidenceViewerProps {
  importId: string;
  evidence: CandidateEvidence[];
  documentTitles: ReadonlyMap<string, string>;
}

export function EvidenceViewer({ importId, evidence, documentTitles }: EvidenceViewerProps) {
  const [selected, setSelected] = useState<CandidateEvidence | null>(null);

  if (!evidence.length) return <p className="empty-hint">Keine Quellen hinterlegt.</p>;

  const documentTitle = selected
    ? documentTitles.get(selected.document_id) ?? selected.document_id
    : null;

  return (
    <section className="declaration-evidence" aria-label="Belege">
      <h4>Quellen</h4>
      <ul className="declaration-evidence-list">
        {evidence.map((item) => {
          const title = documentTitles.get(item.document_id) ?? item.document_id;
          return (
            <li key={item.id}>
              <button
                type="button"
                className="btn"
                onClick={() => setSelected(item)}
                aria-label={`Quelle auf Seite ${item.pdf_page}: ${title}`}
              >
                {title}, Seite {item.pdf_page}
              </button>
              <p>{item.quote}</p>
            </li>
          );
        })}
      </ul>
      {selected && documentTitle && (
        <iframe
          className="declaration-evidence-frame"
          title={`Originalquelle: ${documentTitle}`}
          src={declarationImportDocumentContentUrl(importId, selected.document_id, selected.pdf_page)}
        />
      )}
    </section>
  );
}
