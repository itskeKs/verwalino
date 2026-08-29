import { useId, useState } from "react";
import type { KnowledgeSource, PropertyRule } from "../../api/types";
import { KnowledgeSourceButton } from "./KnowledgeSourceButton";

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "–";
  if (Array.isArray(value)) return value.map(displayValue).join(", ");
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${displayValue(item)}`).join(" · ");
  return String(value);
}

function statusLabel(status: PropertyRule["status"]): string {
  if (status === "NEEDS_CLARIFICATION") return "⚠ Klärungsbedarf";
  if (status === "EFFECTIVE") return "✓ Geltend";
  if (status === "SUPERSEDED") return "Historisch · abgelöst";
  return "Zurückgezogen";
}

export function RuleCard({ rule, onSource }: { rule: PropertyRule; onSource: (source: KnowledgeSource) => void }) {
  const [expanded, setExpanded] = useState(true);
  const detailsId = useId();
  return <article className="knowledge-rule" aria-label={`Regel ${rule.title}`}>
    <header>
      <div>
        <h3><button type="button" className="knowledge-rule-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} aria-controls={detailsId} aria-label={`${rule.title} ein-/ausklappen`}>{rule.title}</button></h3>
        <p>{rule.summary}</p>
      </div>
      <span className={`knowledge-status knowledge-status--${rule.status.toLowerCase()}`} role="status">{statusLabel(rule.status)}</span>
    </header>
    {expanded && <div id={detailsId} className="knowledge-rule-expanded">
      <dl className="knowledge-rule-details">
        <div><dt>Geltungsbereich</dt><dd>{displayValue(rule.scope)}</dd></div>
        <div><dt>Regelung</dt><dd>{displayValue(rule.effect)}</dd></div>
        <div><dt>Ausnahmen</dt><dd>{displayValue(rule.effect.exceptions)}</dd></div>
        <div><dt>Gültigkeit</dt><dd>{rule.status}</dd></div>
      </dl>
      {rule.sources.length > 0 && <div className="knowledge-sources">
        <strong>Quellen</strong>
        {rule.sources.map((source) => <KnowledgeSourceButton key={source.id} source={source} onSource={onSource} />)}
      </div>}
    </div>}
  </article>;
}
