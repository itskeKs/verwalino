import type { KnowledgeSource } from "../../api/types";

function sourceLabel(source: KnowledgeSource): string {
  return [
    source.document_title,
    source.section,
    `PDF-Seite ${source.pdf_page}`,
    source.printed_page ? `gedruckte Seite ${source.printed_page}` : null,
  ].filter((part): part is string => Boolean(part)).join(" · ");
}

export function KnowledgeSourceButton({
  source,
  onSource,
}: {
  source: KnowledgeSource;
  onSource: (source: KnowledgeSource) => void;
}) {
  const label = sourceLabel(source);
  return (
    <button
      type="button"
      className="link-button"
      onClick={() => onSource(source)}
      aria-label={`Quelle auf Seite ${source.pdf_page}: ${label}`}
    >
      {label}
    </button>
  );
}
