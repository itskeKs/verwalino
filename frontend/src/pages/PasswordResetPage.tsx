import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { PasswordRecoveryCard } from "../components/PasswordRecoveryCard";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

const TOKEN_KEY = "verwalino.password-reset-token";

export function PasswordResetPage() {
  const [params, setParams] = useSearchParams();
  const [token, setToken] = useState(() => params.get("token") ?? sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [complete, setComplete] = useState(false);
  const inFlight = useRef(false);

  useEffect(() => {
    if (params.has("token")) {
      sessionStorage.setItem(TOKEN_KEY, params.get("token") ?? "");
      setParams({}, { replace: true });
    }
  }, [params, setParams]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || complete) return;
    setError(null);
    if (password !== repeat) {
      setError("Die Passwörter stimmen nicht überein. Bitte prüfen Sie Ihre Eingabe.");
      return;
    }
    inFlight.current = true; setBusy(true);
    try {
      await api.post("/api/auth/password-reset/confirm", { token, password });
      sessionStorage.removeItem(TOKEN_KEY); setToken(""); setPassword(""); setRepeat("");
      setComplete(true);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 403) {
        sessionStorage.removeItem(TOKEN_KEY); setToken("");
      }
      setError(failure instanceof ApiError ? failure.message : "Zurücksetzen derzeit nicht verfügbar. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false; setBusy(false);
    }
  }
  return <PasswordRecoveryCard title="Passwort zurücksetzen" description="Wählen Sie ein neues Passwort. Anschließend melden Sie sich damit an.">
    {complete ? <>
      <p role="status">Ihr Passwort wurde geändert. Alle bisherigen Sitzungen wurden beendet.</p>
      <p><a className="link" href="/login">Zur Anmeldung</a></p>
    </> : <>
      <form className="space-y-4" onSubmit={submit} aria-busy={busy} aria-describedby={error ? "reset-error" : undefined}>
        {error && <p id="reset-error" role="alert" className="break-words text-sm text-destructive">{error}</p>}
        <fieldset disabled={busy} className="min-w-0 space-y-4">
          <div className="space-y-2"><Label htmlFor="reset-password">Neues Passwort</Label><Input id="reset-password" type="password" className="h-11" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} maxLength={128} autoComplete="new-password" aria-describedby="reset-password-help" required /><p id="reset-password-help" className="text-sm text-muted-foreground">Verwenden Sie 12 bis 128 Zeichen.</p></div>
          <div className="space-y-2"><Label htmlFor="reset-repeat">Passwort wiederholen</Label><Input id="reset-repeat" type="password" className="h-11" value={repeat} onChange={(e) => setRepeat(e.target.value)} minLength={12} maxLength={128} autoComplete="new-password" required /></div>
          <Button type="submit" size="lg" className="w-full">{busy ? "Passwort wird gespeichert …" : "Passwort speichern"}</Button>
        </fieldset>
      </form>
      <p><Link className="link" to="/passwort-vergessen">Neuen Link anfordern</Link></p>
    </>}
  </PasswordRecoveryCard>;
}
