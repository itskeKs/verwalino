import type { KnowledgeAnswerOut, KnowledgeSource } from "../../api/types";
import { KnowledgeSourceButton } from "./KnowledgeSourceButton";

const STATUS_COPY: Record<KnowledgeAnswerOut["status"], string> = {
  ANSWER: "Antwort aus freigegebenen Unterlagen",
  NOT_DOCUMENTED: "In den freigegebenen Unterlagen nicht dokumentiert",
  NEEDS_CLARIFICATION: "Klärung erforderlich",
};

export function KnowledgeAnswer({ answer, onSource }: { answer: KnowledgeAnswerOut; onSource: (source: KnowledgeSource) => void }) {
  return <section className="knowledge-answer" aria-live="polite">
    <h2 tabIndex={-1} id="knowledge-answer-heading">{STATUS_COPY[answer.status]}</h2>
    <p>{answer.answer}</p>
    <p className="knowledge-answer-meta">Belegte Fakten: {answer.fact_ids.length} · Regeln: {answer.rule_ids.length}</p>
    {answer.sources.length > 0 && <div className="knowledge-sources"><strong>Quellen</strong>{answer.sources.map((source) => <KnowledgeSourceButton key={source.id} source={source} onSource={onSource} />)}</div>}
  </section>;
}
