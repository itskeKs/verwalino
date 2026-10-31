import { type FormEvent, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { PasswordRecoveryCard } from "../components/PasswordRecoveryCard";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function PasswordResetRequestPage() {
  const [email, setEmail] = useState("");
  const [website, setWebsite] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError(null);
    try {
      const answer = await api.post<{ message: string }>("/api/auth/password-reset", { email, website });
      setReceipt(answer.message);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Anfrage derzeit nicht verfügbar. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false; setBusy(false);
    }
  }
  return <PasswordRecoveryCard title="Passwort vergessen?" description="Geben Sie Ihre E-Mail-Adresse ein, um einen Link zum Zurücksetzen Ihres Passworts anzufordern.">
    {error && <p id="reset-request-error" role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {receipt ? <>
      <p role="status" className="break-words">{receipt}</p>
      <p className="text-sm text-muted-foreground">Bitte prüfen Sie auch Ihren Spam-Ordner. Der Link ist 1 Stunde gültig.</p>
    </> : <form className="space-y-4" onSubmit={submit} aria-busy={busy} aria-describedby={error ? "reset-request-error" : undefined}>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <div className="space-y-2"><Label htmlFor="reset-email">E-Mail</Label><Input id="reset-email" type="email" className="h-11" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} autoComplete="email" required /></div>
        <div className="hidden" aria-hidden="true"><Label htmlFor="reset-website">Website</Label><Input id="reset-website" name="website" value={website} onChange={(e) => setWebsite(e.target.value)} tabIndex={-1} autoComplete="off" /></div>
        <Button type="submit" size="lg" className="w-full">{busy ? "Anfrage läuft …" : "Link anfordern"}</Button>
      </fieldset>
    </form>}
    <p><Link className="link" to="/login">Zur Anmeldung</Link></p>
  </PasswordRecoveryCard>;
}
