import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { PasswordRecoveryCard } from "../components/PasswordRecoveryCard";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

const TOKEN_KEY = "verwalino.invitation-token";

export function InvitationAcceptPage() {
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
      await api.post("/api/auth/invitation/accept", { token, password });
      sessionStorage.removeItem(TOKEN_KEY); setToken(""); setPassword(""); setRepeat("");
      setComplete(true);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 403) {
        sessionStorage.removeItem(TOKEN_KEY); setToken("");
      }
      setError(failure instanceof ApiError ? failure.message : "Ihr Zugang kann derzeit nicht eingerichtet werden. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false; setBusy(false);
    }
  }
  return <PasswordRecoveryCard title="Einladung annehmen" description="Legen Sie Ihr Passwort fest, um Ihren Zugang zur Organisation einzurichten.">
    {complete ? <>
      <p role="status">Ihr Zugang ist eingerichtet. Sie können sich jetzt anmelden.</p>
      <p><a className="link" href="/login">Zur Anmeldung</a></p>
    </> : <>
      <form className="space-y-4" onSubmit={submit} noValidate aria-busy={busy} aria-describedby={error ? "invite-error" : undefined}>
        {error && <p id="invite-error" role="alert" className="break-words text-sm text-destructive">{error}</p>}
        <fieldset disabled={busy} className="min-w-0 space-y-4">
          <div className="space-y-2"><Label htmlFor="invite-password">Passwort</Label><Input id="invite-password" type="password" className="h-11" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" aria-describedby="invite-password-help" required /><p id="invite-password-help" className="text-sm text-muted-foreground">Verwenden Sie 12 bis 128 Zeichen.</p></div>
          <div className="space-y-2"><Label htmlFor="invite-repeat">Passwort wiederholen</Label><Input id="invite-repeat" type="password" className="h-11" value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" required /></div>
          <Button type="submit" size="lg" className="w-full">{busy ? "Passwort wird gespeichert …" : "Passwort festlegen"}</Button>
        </fieldset>
      </form>
      <p><Link className="link" to="/login">Zur Anmeldung</Link></p>
    </>}
  </PasswordRecoveryCard>;
}
