import { Check, LayoutDashboard, Building2, Inbox, House } from "lucide-react";
import { useState } from "react";
import { useAuth } from "../auth/useAuth";
import { useDesignTheme } from "../design/useDesignTheme";
import type { DesignTheme } from "../api/types";
import { Button } from "../components/ui/button";
import { OrganizationAiSettingsSection } from './OrganizationAiSettingsSection';
import { LegalAssentsSection } from './LegalAssentsSection';

export function SettingsPage() {
  const { session } = useAuth();
  return <PersonalDesign key={`${session?.organization_id}:${session?.user_id}`} />;
}

function PersonalDesign() {
  const { session } = useAuth();
  const { theme, loaded, loading, saving, error, save, reload } = useDesignTheme();
  const [draft, setDraft] = useState<DesignTheme | null>(null);
  const [saved, setSaved] = useState(false);
  const selected = draft ?? theme;
  const changed = selected !== theme;
  async function submit() {
    setSaved(false);
    if (await save(selected)) { setSaved(true); setDraft(null); }
  }
  return <div>
    <header className="page-heading"><div><p className="section-label">Ihr Benutzerkonto</p><h1>Einstellungen</h1><p className="text-muted-foreground">Ihr Design. Für Ihre tägliche Arbeit.</p></div></header>
    <section className="card personal-design" aria-labelledby="design-heading">
      <h2 id="design-heading">Persönliches Design</h2>
      <p className="text-sm text-muted-foreground">Die Auswahl gilt nur für Ihr Benutzerkonto. Andere Personen Ihrer Organisation behalten ihr eigenes Design. Alle Funktionen bleiben verfügbar.</p>
      {loading && <p role="status" className="mt-4">Design wird geladen …</p>}
      {error && !loaded && <div role="alert" className="error-text mt-4">{error}<Button variant="outline" className="ml-3" onClick={reload}>Erneut laden</Button></div>}
      <fieldset className="theme-options" disabled={!loaded || saving}>
        <legend className="sr-only">Design auswählen</legend>
        {(["klar", "warm"] as const).map(option => <label className={`theme-option theme-option--${option}`} key={option}>
          <div className="theme-mini" aria-hidden="true">
            <div className="theme-mini-nav"><House size={15} /><span>verwalino</span><LayoutDashboard size={14} /><Building2 size={14} /><Inbox size={14} /></div>
            <div className="theme-mini-work"><span className="theme-mini-title" /><div className="theme-mini-columns"><div><span /><span /></div><div><span /><span /><span /></div></div></div>
          </div>
          <div className="theme-option-caption"><input type="radio" name="design-theme" value={option} checked={selected === option} onChange={() => { setDraft(option); setSaved(false); }} /><div><strong>{option === "klar" ? "Klar" : "Warm"}</strong><p>{option === "klar" ? "Klare Struktur, blaue Akzente und seitliche Navigation." : "Ruhige Flächen, Petrol und horizontale Navigation."}</p></div>{selected === option && <Check className="text-primary" size={18} aria-hidden="true" />}</div>
        </label>)}
      </fieldset>
      <div className="theme-save-row"><Button onClick={() => void submit()} disabled={!loaded || !changed || saving}>{saving ? "Wird gespeichert …" : "Design speichern"}</Button>{error && loaded && <div role="alert" className="error-text">{error}</div>}<p role="status">{saved ? "Design für Ihr Benutzerkonto gespeichert." : changed ? "Noch nicht gespeichert." : loaded ? "Das gespeicherte Design ist aktiv." : ""}</p></div>
    </section>
    {session?.role === 'OWNER_ADMIN' && <OrganizationAiSettingsSection key={session.organization_id} organizationId={session.organization_id} />}
    {session?.role === 'OWNER_ADMIN' && <LegalAssentsSection />}
  </div>;
}
