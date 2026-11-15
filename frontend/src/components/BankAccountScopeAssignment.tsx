import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { assignBankAccountScope } from "../api/endpoints";
import type { BankAccount } from "../api/types";
import { AccountingScopeSelector } from "./AccountingScopeSelector";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

/** Refusals are told by status, never by the server's wording. */
function refusal(err: unknown): string {
  if (!(err instanceof ApiError)) return "Die Zuordnung konnte nicht gespeichert werden. Bitte erneut versuchen.";
  if (err.status === 403) return "Nur Inhaber/Admin dürfen einen Abrechnungskreis zuordnen.";
  if (err.status === 404) return "Bankkonto oder Abrechnungskreis ist nicht verfügbar. Bitte die Seite neu laden und das Recht „Vorbereiten“ für den Abrechnungskreis prüfen.";
  if (err.status === 409) return "Dieses Bankkonto hat bereits einen Abrechnungskreis. Bitte die Seite neu laden.";
  if (err.status === 422) return "Der Abrechnungskreis passt nicht zu Objekt, Kontotyp oder Mandat dieses Bankkontos.";
  return "Die Zuordnung konnte nicht gespeichert werden. Bitte erneut versuchen.";
}

/** One-time classification of an account saved without an Abrechnungskreis (administrators only). */
export function BankAccountScopeAssignment({ account, onAssigned }: { account: BankAccount; onAssigned: () => void }) {
  const [open, setOpen] = useState(false);
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [valid, setValid] = useState(true);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busyRef = useRef(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busyRef.current || !scopeId || !valid) return;
    busyRef.current = true; setBusy(true); setError(null);
    try {
      await assignBankAccountScope(scopeId, account.id, reason.trim());
      setOpen(false);
      onAssigned();
    } catch (err) {
      setError(refusal(err));
    } finally {
      busyRef.current = false; setBusy(false);
    }
  }

  return <>
    <button type="button" className="btn btn--small" onClick={() => { setScopeId(null); setReason(""); setError(null); setOpen(true); }}>Abrechnungskreis zuordnen</button>
    <Dialog open={open} onOpenChange={value => { if (!busyRef.current) setOpen(value); }}>
      <DialogContent onEscapeKeyDown={event => { if (busyRef.current) event.preventDefault(); }} onPointerDownOutside={event => { if (busyRef.current) event.preventDefault(); }}>
        <DialogTitle>Abrechnungskreis zuordnen</DialogTitle>
        <DialogDescription>
          Bankkonto „{account.label}“ einem Abrechnungskreis zuordnen. Die Zuordnung wird mit Ihrer Begründung festgehalten und kann nicht geändert werden.
          Bereits gebuchte Zahlungen und Abrechnungen bleiben unverändert. Danach sehen nur Teammitglieder mit Leserecht für den Abrechnungskreis dieses Konto.
        </DialogDescription>
        <form onSubmit={submit} className="form-grid">
          <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0, display: "contents" }}>
            <AccountingScopeSelector propertyId={account.property_id}
              kind={account.account_type === "KAUTION" ? "MIET" : account.account_type === "RUECKLAGE" ? "WEG" : undefined}
              value={scopeId} onChange={setScopeId} onValidityChange={setValid} />
            <p className="muted form-full-width">Angeboten werden Abrechnungskreise dieses Objekts, für die Sie das Recht „Vorbereiten“ haben. Fehlt der passende, legen Sie ihn unter <Link className="link" to="/berechtigungen/teamrechte">Teamrechte</Link> an und erteilen Sie sich das Recht.</p>
            <label className="form-full-width">Begründung *
              <input required maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} placeholder="z. B. Konto der Eigentümergemeinschaft" />
            </label>
            {error && <p role="alert" className="error-text form-full-width">{error}</p>}
            <div className="form-actions">
              <button type="submit" className="btn btn--primary" disabled={!scopeId || !valid || !reason.trim()}>{busy ? "Speichern…" : "Zuordnung speichern"}</button>
              <button type="button" className="btn" onClick={() => setOpen(false)}>Abbrechen</button>
            </div>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
