import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getPropertyKnowledge, propertyDocumentContentUrl, queryPropertyKnowledge } from "../api/endpoints";
import type { KnowledgeAnswerOut, KnowledgeSource, PropertyKnowledgeOverview } from "../api/types";
import { ApiError } from "../api/client";
import { PropertyDocuments } from "../components/knowledge/PropertyDocuments";
import { RuleCard } from "../components/knowledge/RuleCard";
import { KnowledgeSearch } from "../components/knowledge/KnowledgeSearch";
import { KnowledgeAnswer } from "../components/knowledge/KnowledgeAnswer";

export function PropertyKnowledgePage() {
  const { propertyId } = useParams<{ propertyId: string }>();
  const [overview, setOverview] = useState<PropertyKnowledgeOverview | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswerOut | null>(null);
  const [questionError, setQuestionError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [selectedSource, setSelectedSource] = useState<KnowledgeSource | null>(null);
  const [viewerError, setViewerError] = useState(false);
  const sourceTrigger = useRef<HTMLButtonElement | null>(null);
  const answerHeading = useRef<HTMLHeadingElement | null>(null);
  const viewerDialog = useRef<HTMLDialogElement | null>(null);
  const viewerCloseButton = useRef<HTMLButtonElement | null>(null);
  const [viewerKey, setViewerKey] = useState(0);

  const load = useCallback(() => {
    if (!propertyId) return;
    setLoading(true);
    setError(null);
    void getPropertyKnowledge(propertyId, { includeHistory: showHistory })
      .then(setOverview)
      .catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Gebäudewissen konnte nicht geladen werden."))
      .finally(() => setLoading(false));
  }, [propertyId, showHistory]);

  useEffect(() => { void Promise.resolve().then(load); }, [load]);

  useEffect(() => {
    const dialog = viewerDialog.current;
    if (!dialog) return;
    if (selectedSource) {
      try {
        if (!dialog.open) dialog.showModal();
      } catch {
        dialog.setAttribute("open", "");
      }
      viewerCloseButton.current?.focus();
      return;
    }
    if (dialog.open) dialog.close();
    else dialog.removeAttribute("open");
  }, [selectedSource]);

  const openSource = (source: KnowledgeSource, trigger?: HTMLButtonElement) => {
    sourceTrigger.current = trigger ?? (document.activeElement instanceof HTMLButtonElement ? document.activeElement : null);
    setViewerError(false);
    setSelectedSource(source);
  };
  const closeSource = () => {
    setSelectedSource(null);
    window.setTimeout(() => sourceTrigger.current?.focus(), 0);
  };

  async function submitQuestion(event: React.FormEvent) {
    event.preventDefault();
    if (!propertyId || !question.trim()) return;
    setAsking(true);
    setQuestionError(null);
    setAnswer(null);
    try {
      const result = await queryPropertyKnowledge(propertyId, question.trim());
      setAnswer(result);
      window.setTimeout(() => answerHeading.current?.focus(), 0);
    } catch (reason) {
      setQuestionError(reason instanceof ApiError && reason.status === 503
        ? "Die automatisierte Antwort ist vorübergehend nicht verfügbar. Dokumente, Regeln und Suche bleiben nutzbar."
        : reason instanceof ApiError ? reason.message : "Die Frage konnte nicht beantwortet werden.");
    } finally { setAsking(false); }
  }

  if (loading) return <p>Lade Gebäudewissen…</p>;
  if (error || !overview || !propertyId) return <p className="error-text">{error ?? "Objekt nicht gefunden."}</p>;

  const rules = Object.entries(overview.rules_by_topic);
  return <div className="knowledge-page">
    <p><Link to={`/objekte/${propertyId}`} className="link">← Zum Objekt</Link></p>
    <div className="knowledge-page-heading">
      <div><h1>Dokumente &amp; Wissen</h1><p>Freigegebene Teilungserklärungsdaten mit nachvollziehbaren Quellen.</p></div>
      <span className="knowledge-counts">{overview.documents.length} Dokumente · {rules.flatMap(([, topicRules]) => topicRules).length} Regeln</span>
    </div>
    <PropertyDocuments documents={overview.documents} relations={overview.document_relations} />

    <section className="knowledge-section" aria-labelledby="knowledge-rules-heading">
      <div className="knowledge-section-heading"><h2 id="knowledge-rules-heading">Geltende Regeln</h2><button type="button" className="btn btn--small" aria-pressed={showHistory} onClick={() => setShowHistory((value) => !value)}>{showHistory ? "Verlauf ausblenden" : "Verlauf anzeigen"}</button></div>
      {!rules.length ? <p className="empty-hint">Noch keine freigegebenen Regeln vorhanden.</p> : rules.map(([topic, topicRules]) => <section key={topic} className="knowledge-topic" aria-labelledby={`knowledge-topic-${topic}`}>
        <h3 id={`knowledge-topic-${topic}`}>{topicRules[0]?.topic ?? topic}</h3>
        {topicRules.filter((rule) => showHistory || (rule.status !== "SUPERSEDED" && rule.status !== "RETRACTED")).map((rule) => <RuleCard key={rule.id} rule={rule} onSource={openSource} />)}
      </section>)}
    </section>

    <KnowledgeSearch propertyId={propertyId} onSource={openSource} />

    <section className="knowledge-section" aria-labelledby="knowledge-question-heading">
      <h2 id="knowledge-question-heading">Frage an Gebäudewissen</h2>
      <p className="knowledge-disclosure">Automatisierte Zusammenfassung freigegebener Teilungserklärungsdaten. Sie ändert keine Abrechnung, Reparaturzuständigkeit, Verträge oder Stammdaten.</p>
      <form onSubmit={submitQuestion} className="knowledge-question-form">
        <label>Frage an Gebäudewissen<textarea value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={2000} /></label>
        <button className="btn btn--primary" type="submit" disabled={asking || !question.trim()}>{asking ? "Prüfe…" : "Frage stellen"}</button>
      </form>
      {questionError && <p className="error-text" role="alert">{questionError}</p>}
      {answer && <div ref={(node) => { answerHeading.current = node?.querySelector("#knowledge-answer-heading") ?? null; }}><KnowledgeAnswer answer={answer} onSource={openSource} /></div>}
    </section>

    {selectedSource && <dialog ref={viewerDialog} className="knowledge-viewer" aria-label={`Originalquelle: ${selectedSource.document_title}`} onCancel={(event) => { event.preventDefault(); closeSource(); }} onClose={() => { if (selectedSource) closeSource(); }}>
        <header><div><h2>Originalquelle</h2><p>{selectedSource.document_title}{selectedSource.section ? ` · ${selectedSource.section}` : ""}{selectedSource.printed_page ? ` · gedruckte Seite ${selectedSource.printed_page}` : ""}</p></div><button ref={viewerCloseButton} type="button" className="btn" onClick={closeSource}>Schließen</button></header>
        <p>{selectedSource.quote}</p>
        {viewerError && <p className="error-text" role="alert">Das PDF konnte nicht geladen werden. Die Quellenangaben bleiben verfügbar.</p>}
        <div className="knowledge-viewer-actions"><button type="button" className="btn btn--small" onClick={() => { setViewerError(false); setViewerKey((value) => value + 1); }}>PDF neu laden</button><a className="btn btn--small" href={propertyDocumentContentUrl(propertyId, selectedSource.document_id, selectedSource.pdf_page)} target="_blank" rel="noreferrer">PDF separat öffnen</a></div>
        <iframe key={viewerKey} title={`Originalquelle: ${selectedSource.document_title}`} src={propertyDocumentContentUrl(propertyId, selectedSource.document_id, selectedSource.pdf_page)} onError={() => setViewerError(true)} onErrorCapture={() => setViewerError(true)} />
    </dialog>}
  </div>;
}
