import { useEffect, useState } from "react";
import { api } from "../api/client";
import { sourceSecurityGuidance, sourceSecurityLabels, type SourceSecurityStatus } from "../lib/sourceSecurity";

export function SourceSecurityNotice() {
  const [state, setState] = useState<SourceSecurityStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api.get<SourceSecurityStatus>("/api/access-preparation/source-security", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setState(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  if (error) return <p role="alert" className="error-text">Der aktuelle Quellschutz ist nicht verfügbar. Bitte die Seite erneut laden.</p>;
  if (!state) return <p role="status">Quellschutz wird geladen…</p>;
  return <div className="space-y-2"><p><strong>{sourceSecurityLabels[state.mode]}</strong></p><p>{sourceSecurityGuidance[state.mode].meaning}</p></div>;
}
