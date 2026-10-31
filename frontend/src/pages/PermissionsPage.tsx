import { useEffect, useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import type { AccessPreparation } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { cn } from "../lib/utils";
import { sourceSecurityGuidance, sourceSecurityLabels, type SourceSecurityStatus } from "../lib/sourceSecurity";
import { SourceAdmissionReviewsPage } from "./SourceAdmissionReviewsPage";
import { DunningQuarantinePage } from "./DunningQuarantinePage";
import { MandateQuarantinePage } from "./MandateQuarantinePage";

export function PermissionsLayout() {
  const { session } = useAuth();
  return <div className="min-w-0 space-y-6">
    <header className="space-y-3">
      <h1>Berechtigungen</h1>
      {session?.role === "OWNER_ADMIN" && <>
        <p>Verwalten Sie, wer Objektdaten, Quellen und Finanzbereiche sehen und bearbeiten darf.</p>
        <nav aria-label="Berechtigungsbereiche" className="flex flex-wrap gap-2">
          {[["/berechtigungen", "Übersicht"], ["/berechtigungen/teamrechte", "Teamrechte"], ["/berechtigungen/bestand", "Bestand prüfen"], ["/berechtigungen/pruefungen", "Offene Prüfungen"]].map(([to, label]) =>
            <NavLink key={to} to={to} end={to === "/berechtigungen"} className={({ isActive }) => cn("rounded-md border px-3 py-2 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring", isActive ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card text-foreground hover:bg-accent")}>{label}</NavLink>)}
        </nav>
        <p className="flex flex-wrap gap-x-4 gap-y-2 text-sm"><Link className="link" to="/kontaktdaten-rechte">Kontaktdaten-Rechte</Link><Link className="link" to="/gebaeudedaten-rechte">Gebäudedaten-Rechte</Link></p>
      </>}
    </header>
    <Outlet />
  </div>;
}

const metrics: [keyof AccessPreparation["counts"], string][] = [
  ["properties_without_domain", "Objekte ohne Datenbereich"], ["properties_without_current_reader", "Objekte ohne Lesefreigabe"],
  ["contacts_without_domain", "Kontakte ohne gemeinsamen Datenbereich"], ["contacts_without_current_reader", "Kontakte ohne berechtigten Leser"],
  ["contacts_unknown_creator", "Kontakte mit unbekanntem Ersteller"],
  ["originals_quarantined", "Gebäudeoriginale in Quarantäne"], ["bound_originals_without_review_record", "Zugeordnete Originale ohne Prüfvermerk"],
  ["unbound_originals_unknown_uploader", "Nicht zugeordnete Originale mit unbekannter Herkunft"],
  ["bank_accounts_without_scope", "Konten ohne Abrechnungskreis"], ["payments_without_scope", "Zahlungen ohne Abrechnungskreis"],
  ["cost_entries_without_scope", "Kosten ohne Abrechnungskreis"], ["settlements_without_scope", "Abrechnungen ohne Abrechnungskreis"],
  ["economic_plans_without_scope", "Wirtschaftspläne ohne Abrechnungskreis"], ["dunning_without_context", "Mahnungen ohne geprüften Ursprung"],
  ["mandate_originals_without_scope", "Mandatsunterlagen ohne Abrechnungskreis"],
];

function Overview() {
  const [state, setState] = useState<SourceSecurityStatus | null>(null);
  const [inventory, setInventory] = useState<AccessPreparation | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [inventoryError, setInventoryError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      api.get<SourceSecurityStatus>("/api/access-preparation/source-security").then(value => { if (active) setState(value); }).catch(() => { if (active) setStatusError(true); }),
      api.get<AccessPreparation>("/api/access-preparation").then(value => { if (active) setInventory(value); }).catch(() => { if (active) setInventoryError(true); }),
    ]).then(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision]);
  function reload() {
    setState(null); setInventory(null); setStatusError(false); setInventoryError(false); setLoading(true); setRevision(value => value + 1);
  }
  const guidance = state ? sourceSecurityGuidance[state.mode] : null;
  return <div className="space-y-6">
    <Card><CardHeader><h2>Quellschutz für Ihr Team</h2></CardHeader><CardContent className="space-y-3">
      {loading && <p role="status">Status und Bestand werden geladen…</p>}
      {statusError && <p role="alert" className="error-text">Der aktuelle Quellschutz ist nicht verfügbar. Bitte erneut laden.</p>}
      {state && guidance && <>
        <Badge tone={guidance.tone}>{sourceSecurityLabels[state.mode]}</Badge>
        <p>{guidance.meaning}</p>
        <h3 className="font-semibold">Nächster Schritt</h3><p>{guidance.next}</p>
        <Link className="link" to={guidance.to}>Zum nächsten Schritt</Link>
      </>}
      <Button variant="outline" disabled={loading} onClick={reload}>Status und Bestand neu laden</Button>
    </CardContent></Card>
    <Card><CardHeader><h2>Offene Punkte im Bestand</h2><p className="text-sm text-muted-foreground">Kategorien können sich überschneiden. Diese Zahlen bestätigen keine Freigabe oder vollständige Prüfung.</p></CardHeader><CardContent>
      {inventoryError && <p role="alert" className="error-text">Die Bestandszahlen sind nicht verfügbar. Bitte erneut laden.</p>}
      {inventory && <>
        <p className="mb-4 text-sm text-muted-foreground">Stand: {new Date(inventory.generated_at).toLocaleString("de-DE")}</p>
        <dl className="grid gap-x-8 gap-y-3 lg:grid-cols-2">{metrics.map(([key, label]) => <div key={key} className="flex min-w-0 items-start justify-between gap-4"><dt>{label}</dt><dd className="shrink-0 font-semibold tabular-nums">{inventory.counts[key].toLocaleString("de-DE")}</dd></div>)}</dl>
      </>}
    </CardContent></Card>
  </div>;
}

export function PermissionsOverview() {
  const { session } = useAuth();
  return session?.role === "OWNER_ADMIN" ? <Overview key={session.organization_id} /> : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
}

export function PermissionsReviews() {
  const { session } = useAuth();
  if (session?.role !== "OWNER_ADMIN") return <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
  return <div className="space-y-6">
    <h2>Offene Prüfungen</h2>
    <p>Die Listen zeigen die jeweils geladenen Einträge. Laden Sie weitere Seiten für den vollständigen Bestand. Prüfvermerke und Quarantäne erteilen keine Teamrechte.</p>
    <SourceAdmissionReviewsPage />
    <DunningQuarantinePage />
    <MandateQuarantinePage />
  </div>;
}
