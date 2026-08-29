import type { DocumentRelation, PropertyKnowledgeDocument } from "../../api/types";

const DOC_TYPE_LABELS: Record<string, string> = {
  DECLARATION: "Teilungserklärung",
  AMENDMENT: "Nachtrag",
  ANNEX: "Anlage",
  OTHER: "Weitere Unterlage",
};

interface PropertyDocumentsProps {
  documents: PropertyKnowledgeDocument[];
  relations: DocumentRelation[];
}

export function PropertyDocuments({ documents, relations }: PropertyDocumentsProps) {
  const titles = new Map(documents.map((document) => [document.id, document.title]));
  return (
    <section className="knowledge-section" aria-labelledby="knowledge-documents-heading">
      <h2 id="knowledge-documents-heading">Dokumente</h2>
      {!documents.length ? <p className="empty-hint">Noch keine freigegebenen Dokumente vorhanden.</p> : (
        <ul className="knowledge-document-list">
          {documents.map((document) => {
            const documentRelations = relations.filter((relation) => relation.source_document_id === document.id || relation.target_document_id === document.id);
            return <li key={document.id} className="knowledge-document">
              <strong>{document.title}</strong>
              <span>{DOC_TYPE_LABELS[document.doc_type] ?? document.doc_type}</span>
              {document.document_date && <span>vom {new Intl.DateTimeFormat("de-DE").format(new Date(document.document_date))}</span>}
              {document.deed_reference && <span>{document.deed_reference}</span>}
              <span>{document.page_count} Seiten</span>
              {documentRelations.map((relation) => {
                const counterpart = relation.source_document_id === document.id ? relation.target_document_id : relation.source_document_id;
                return <span key={relation.id} className="knowledge-relation">{relation.relation_type}: {titles.get(counterpart) ?? counterpart}</span>;
              })}
            </li>;
          })}
        </ul>
      )}
    </section>
  );
}
