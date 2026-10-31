import { useState } from "react";
import { api } from "../api/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "./ui/dialog";

import { lifecycleChanged } from "../api/organizationLifecycle";

interface Preview {
  organization_name: string;
  notice: string;
  retention: {
    retained: Record<string, { count: number; until: string; basis: string }>;
    erased: { person: number };
    erased_fields: string[];
  };
}
const labels: Record<string, string> = {
  audit_event: "Änderungsnachweise", cost_entry: "Bestätigte Kosten", payment: "Gebuchte Zahlungen",
  invoice_document: "Rechnungsoriginale", settlement_run: "Finalisierte Abrechnungen", settlement_line: "Abrechnungspositionen",
  economic_plan: "Finalisierte Wirtschaftspläne", economic_plan_line: "Planpositionen", dunning_notice: "Ausgestellte Mahnungen",
  attachment: "Gespeicherte Anhänge", letter_document: "Ausgestellte Schreiben", document_ref: "Originalnachweise",
  property_document: "Objektunterlagen", mandate_document: "Mandatsunterlagen", person: "Gebundene Personenstammdaten",
  property: "Erforderliche Objektdaten", unit: "Erforderliche Einheiten", user: "Pseudonymisierte Nutzerkennungen",
  bank_account: "Erforderliche Bankkonten", lease_agreement: "Erforderliche Mietverträge", tenant_profile: "Mieterzuordnungen",
  owner_profile: "Eigentümerzuordnungen", vendor: "Lieferantenzuordnungen", ownership_history: "Eigentumsnachweise",
};

export function OrganizationDeletion({ changed, download, exporting }: { changed: () => void; download: () => Promise<void>; exporting: boolean }) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function request() {
    setBusy(true); setError(null);
    try { setPreview(await api.post<Preview>("/api/organizations/deletion")); setName(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Die Vorschau konnte nicht geladen werden."); }
    finally { setBusy(false); }
  }
  async function confirm() {
    if (!preview || name !== preview.organization_name || busy) return;
    setBusy(true); setError(null);
    try {
      await api.post("/api/organizations/deletion/confirm", { organization_name: name });
      setPreview(null); changed(); lifecycleChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Die Löschung konnte nicht bestätigt werden."); }
    finally { setBusy(false); }
  }
  return <>
    <Button type="button" variant="destructive" size="lg" disabled={busy} onClick={() => void request()}>Organisation löschen</Button>
    {error && !preview && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <Dialog open={preview !== null} onOpenChange={open => { if (!open && !busy) setPreview(null); }}>
      <DialogContent className="max-h-[90dvh] !max-w-[calc(100vw-2rem)] overflow-y-auto transition-none sm:!max-w-lg">
        <DialogHeader><DialogTitle>Organisation löschen</DialogTitle><DialogDescription>Prüfen Sie die Aufbewahrung und sichern Sie Ihre Daten vor der Bestätigung.</DialogDescription></DialogHeader>
        {preview && <>
          <p className="text-sm">{preview.notice}</p>
          <div className="rounded-md border border-border bg-secondary/40 p-3 space-y-2">
            <p className="text-sm font-semibold">Wir empfehlen dringend einen frischen Export, bevor Sie die Löschung bestätigen.</p>
            <Button type="button" variant="outline" className="h-auto min-h-11 whitespace-normal" disabled={exporting || busy} onClick={() => void download()}>{exporting ? "Export wird vorbereitet …" : "Jetzt frischen Export erstellen"}</Button>
          </div>
          <h3 className="font-semibold">Aufbewahrungspflichtige Unterlagen</h3>
          <ul className="space-y-1 text-sm break-words">
            {Object.entries(preview.retention.retained).map(([entity, row]) => <li key={entity}>{labels[entity] ?? "Weitere historische Nachweise"}: {row.count.toLocaleString("de-DE")} bis {new Date(`${row.until}T12:00:00`).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" })}</li>)}
          </ul>
          <p className="text-sm">{preview.retention.erased.person.toLocaleString("de-DE")} Personen werden ohne laufende Aufbewahrungspflicht anonymisiert. Stammdaten mit laufender Frist bleiben unter Verarbeitungseinschränkung erhalten. Nutzerkontakte und Anmeldedaten werden gelöscht.</p>
          <ul className="list-disc pl-5 text-sm">{preview.retention.erased_fields.map(field => <li key={field}>{field}</li>)}</ul>
          <p className="text-sm">Nach der Bestätigung beginnt eine siebentägige Widerrufsfrist. Ihre Organisation ist währenddessen schreibgeschützt. Sie können die Löschung in dieser Frist widerrufen.</p>
          <label className="space-y-2 text-sm">Organisationsname zur Bestätigung<Input value={name} onChange={event => setName(event.target.value)} autoComplete="off" /></label>
          <p className="text-sm text-muted-foreground break-words">Bitte geben Sie „{preview.organization_name}“ exakt ein.</p>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="button" variant="destructive" disabled={busy || exporting || name !== preview.organization_name} onClick={() => void confirm()}>Löschung bestätigen</Button>
        </>}
      </DialogContent>
    </Dialog>
  </>;
}
