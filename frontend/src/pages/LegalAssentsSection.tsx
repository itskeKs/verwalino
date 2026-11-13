import { useEffect, useState } from "react";
import { api } from "../api/client";

type Assent = { document: string; version: string; assented_at: string; admin_name: string };

const DOCUMENT_LABELS: Record<string, string> = {
  TERMS: "Nutzungsbedingungen",
  AV: "Auftragsverarbeitungsvertrag",
};

export function LegalAssentsSection() {
  const [assents, setAssents] = useState<Assent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.get<Assent[]>("/api/organizations/legal-assents").then(
      (rows) => setAssents(rows),
      () => setError("Die Zustimmungen konnten nicht geladen werden."),
    );
  }, []);
  return <section className="card" aria-labelledby="legal-assents-heading">
    <h2 id="legal-assents-heading">Rechtliches</h2>
    <p className="text-sm text-muted-foreground">Welche Versionen Ihre Organisation akzeptiert hat, wann und durch wen. Diese Angaben sind nur zur Ansicht.</p>
    {error && <div role="alert" className="error-text mt-4">{error}</div>}
    {!assents && !error && <p role="status" className="mt-4">Wird geladen …</p>}
    {assents && <ul className="mt-4 space-y-2 text-sm">
      {assents.map((assent) => <li key={assent.document}>
        <strong>{DOCUMENT_LABELS[assent.document] ?? assent.document}</strong>
        {" — Version "}{assent.version}{", bestätigt am "}
        {new Date(assent.assented_at).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" })}
        {" durch "}{assent.admin_name}
      </li>)}
      {assents.length === 0 && <li>Keine Zustimmungen aufgezeichnet.</li>}
    </ul>}
  </section>;
}
