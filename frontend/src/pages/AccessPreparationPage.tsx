import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AccessPreparation } from "../api/types";
import { useAuth } from "../auth/useAuth";

type Metric = [keyof AccessPreparation["counts"], string];
const groups: { title: string; metrics: Metric[]; to: string; action: string }[] = [
  { title: "Gebäudedaten", to: "/gebaeudedaten-rechte", action: "Datenbereiche und Teamrechte prüfen", metrics: [
    ["properties_total", "Objekte insgesamt"], ["properties_without_domain", "Ohne Datenbereich"],
    ["properties_without_current_reader", "Ohne aktuelle Lesefreigabe"],
  ] },
  { title: "Kontaktdaten", to: "/kontaktdaten-rechte", action: "Kontaktdaten-Rechte prüfen", metrics: [
    ["contacts_total", "Kontakte insgesamt"], ["contacts_without_domain", "Ohne gemeinsamen Datenbereich"],
    ["contacts_unknown_creator", "Ursprünglicher Ersteller unbekannt"], ["contacts_without_current_reader", "Ohne aktuell berechtigten Leser"],
  ] },
  { title: "Gebäudeoriginale", to: "/altquellen", action: "Altquellen prüfen", metrics: [
    ["originals_total", "Originale insgesamt"], ["bound_originals_without_review_record", "Zugeordnet, ohne Prüfvermerk"],
    ["originals_quarantined", "In Quarantäne"], ["unbound_originals_unknown_uploader", "Nicht zugeordnet, Herkunft unbekannt"],
  ] },
  { title: "Finanzzuordnung", to: "/berechtigungen", action: "Abrechnungskreise und Zuordnungen prüfen", metrics: [
    ["bank_accounts_without_scope", "Konten ohne Abrechnungskreis"], ["payments_without_scope", "Zahlungen ohne Abrechnungskreis"],
    ["cost_entries_without_scope", "Kosten ohne Abrechnungskreis"], ["settlements_without_scope", "Abrechnungen ohne Abrechnungskreis"],
    ["economic_plans_without_scope", "Wirtschaftspläne ohne Abrechnungskreis"],
  ] },
  { title: "Weitere Altbestände", to: "/berechtigungen", action: "Vorbereitete Bereiche ansehen", metrics: [
    ["dunning_without_context", "Mahnungen ohne geprüften Ursprung"], ["mandate_originals_without_scope", "Mandatsunterlagen ohne Abrechnungskreis"],
  ] },
];

function Inventory() {
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<AccessPreparation | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api.get<AccessPreparation>("/api/access-preparation").then(result => {
      if (active) setData(result);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Die Bestandsübersicht ist nicht verfügbar.");
    }).finally(() => { if (active) setPending(false); });
    return () => { active = false; };
  }, [revision]);
  const refresh = () => {
    setData(null); setError(null); setPending(true); setRevision(value => value + 1);
  };
  return <div className="space-y-6">
    <header><h1>Freigaben vorbereiten</h1>
      <p>Bestandsübersicht für die ausdrückliche Prüfung von Datenbereichen, Quellen und Teamrechten.</p>
    </header>
    <div className="card space-y-2">
      <h2>Aktivierung noch nicht verfügbar</h2>
      <p>Diese Zahlen zeigen vorhandene Zuordnungen. Sie bestätigen weder vollständige Quellenprüfung noch ausreichende Teamrechte. Auch bei null offenen Einträgen wird kein Zugriff freigeschaltet.</p>
      <p>Ein bekannter privater Ersteller kann seinen Kontakt weiterhin lesen. Ein gemeinsamer Datenbereich braucht ausdrückliche Lesefreigaben. Prüfvermerke bei Originalen ersetzen keine aktuelle Quellenprüfung.</p>
      <p>Alte Mahnungen benötigen eine gesonderte Prüfung ihres Ursprungs. Unzugeordnete Mandatsunterlagen bleiben in Quarantäne. Die Übersicht ändert keine Originale.</p>
    </div>
    <p><Link className="link" to="/mandatsquarantaene">Mandatsquarantäne prüfen</Link></p>
    <button className="btn" type="button" onClick={refresh} disabled={pending}>Bestand aktualisieren</button>
    {pending && <p role="status">Bestand wird geprüft…</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    {data && <>
      <p>Stand: {new Date(data.generated_at).toLocaleString("de-DE")} · Zugriffsmodus: {{ PREPARATION: "Vorbereitung", ENFORCED: "Eingeschränkt", PAUSED: "Aktionen pausiert" }[data.mode]}</p>
      <p>Die Kategorien können sich überschneiden. Finanzzahlen enthalten auch Entwürfe und aufbewahrte Einträge.</p>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {groups.map(group => <section className="card min-w-0 space-y-3" key={group.title}>
          <h2>{group.title}</h2>
          <dl className="space-y-2">{group.metrics.map(([key, label]) => <div className="flex items-start justify-between gap-4" key={key}>
            <dt>{label}</dt><dd className="shrink-0 font-semibold tabular-nums">{data.counts[key].toLocaleString("de-DE")}</dd>
          </div>)}</dl>
          <Link to={group.to}>{group.action}</Link>
        </section>)}
      </div>
    </>}
  </div>;
}

export function AccessPreparationPage() {
  const { session } = useAuth();
  return session?.role === "OWNER_ADMIN" ? <Inventory key={session.organization_id} />
    : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
}
