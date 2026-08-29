import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KnowledgeHit, KnowledgeSource } from "../../api/types";
import { searchPropertyKnowledge } from "../../api/endpoints";
import { KnowledgeSourceButton } from "./KnowledgeSourceButton";

export function KnowledgeSearch({ propertyId, onSource }: { propertyId: string; onSource: (source: KnowledgeSource) => void }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const latestRequest = useRef(0);
  const activeController = useRef<AbortController | null>(null);

  const invalidateActiveRequest = useCallback(() => {
    latestRequest.current += 1;
    activeController.current?.abort();
    activeController.current = null;
  }, []);

  useLayoutEffect(() => {
    invalidateActiveRequest();
  }, [invalidateActiveRequest, propertyId]);

  useEffect(() => {
    const requestId = ++latestRequest.current;
    const normalized = query.trim();
    if (!normalized) return;
    const controller = new AbortController();
    activeController.current = controller;
    const timer = window.setTimeout(() => {
      void searchPropertyKnowledge(propertyId, normalized, { includeHistory: false, signal: controller.signal })
        .then((result) => { if (requestId === latestRequest.current) { setHits(result.hits); setError(null); } })
        .catch((reason: unknown) => { if (requestId === latestRequest.current && !(reason instanceof DOMException && reason.name === "AbortError")) setError("Suche konnte nicht geladen werden."); });
    }, 300);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (activeController.current === controller) activeController.current = null;
    };
  }, [propertyId, query]);

  const onChange = (value: string) => {
    invalidateActiveRequest();
    setQuery(value);
    if (!value.trim()) { setHits([]); setError(null); }
  };

  return <section className="knowledge-section" aria-labelledby="knowledge-search-heading">
    <h2 id="knowledge-search-heading">Suche</h2>
    <label className="knowledge-search-label">Gebäudewissen durchsuchen
      <input value={query} onChange={(event) => onChange(event.target.value)} placeholder="z. B. Heizkosten oder 47.732/1.000.000" />
    </label>
    {error && <p role="alert" className="error-text">{error}</p>}
    {hits.length > 0 && <ul className="knowledge-hit-list" aria-label="Suchergebnisse">
      {hits.map((hit) => <li key={hit.id}>
        <strong>{hit.title}</strong> <span className="knowledge-status">{hit.kind === "RULE" ? "Regel" : "Fakt"} · {hit.status}</span>
        <p>{hit.summary}</p>
        <span>{hit.sources.length} Quellen</span>
        {hit.sources.map((source) => <KnowledgeSourceButton key={source.id} source={source} onSource={onSource} />)}
      </li>)}
    </ul>}
  </section>;
}
