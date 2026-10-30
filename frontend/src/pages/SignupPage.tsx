import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useDeploymentMode } from "../auth/useDeploymentMode";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

function SignupCard({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center bg-background p-4 sm:p-6">
    <Card className="w-full min-w-0 max-w-lg">
      <CardHeader><h1 className="break-words text-2xl font-semibold">{title}</h1><CardDescription>{description}</CardDescription></CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  </main>;
}

export function SignupPage() {
  const deployment = useDeploymentMode();
  const [organization, setOrganization] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [website, setWebsite] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (inFlight.current) return;
    setError(null);
    if (!receipt && password !== repeat) {
      setError("Die Passwörter stimmen nicht überein. Bitte prüfen Sie Ihre Eingabe.");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const answer = await api.post<{ message: string }>(receipt ? "/api/signup/resend" : "/api/signup", receipt ? { email } : {
        organization_name: organization, admin_name: name, email, password, website,
      });
      setReceipt(answer.message);
      setPassword(""); setRepeat("");
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Registrierung fehlgeschlagen. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (deployment.mode === null && !deployment.error) return <main className="p-6" role="status">Lade...</main>;
  if (deployment.mode !== "hosted") return <Navigate to="/login" replace />;
  return <SignupCard title="Konto erstellen" description="Registrieren Sie Ihre Organisation. Bestätigen Sie anschließend Ihre E-Mail-Adresse über den Link in der E-Mail.">
    {error && <p id="signup-error" role="alert" className="break-words text-sm text-destructive">{error}</p>}
    {receipt ? <>
      <p role="status" className="break-words">{receipt}</p>
      <p className="text-sm text-muted-foreground">Bitte prüfen Sie auch Ihren Spam-Ordner. Ein neuer Link kann frühestens nach 10 Minuten gesendet werden.</p>
      <Button variant="ghost" className="h-11" disabled={busy} onClick={() => void submit()}>{busy ? "Anfrage läuft …" : "Erneut senden"}</Button>
    </> : <form className="space-y-4" onSubmit={submit} aria-busy={busy} aria-describedby={error ? "signup-error" : undefined}>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <div className="space-y-2"><Label htmlFor="signup-org">Organisation</Label><Input id="signup-org" className="h-11" value={organization} onChange={(e) => setOrganization(e.target.value)} maxLength={255} required autoComplete="organization" /></div>
        <div className="space-y-2"><Label htmlFor="signup-name">Name</Label><Input id="signup-name" className="h-11" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} required autoComplete="name" /></div>
        <div className="space-y-2"><Label htmlFor="signup-email">E-Mail</Label><Input id="signup-email" className="h-11" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} required autoComplete="email" /></div>
        <div className="space-y-2"><Label htmlFor="signup-password">Passwort</Label><Input id="signup-password" className="h-11" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" aria-describedby="signup-password-help" /><p id="signup-password-help" className="text-sm text-muted-foreground">Verwenden Sie 12 bis 128 Zeichen.</p></div>
        <div className="space-y-2"><Label htmlFor="signup-repeat">Passwort wiederholen</Label><Input id="signup-repeat" className="h-11" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" /></div>
        <div className="hidden" aria-hidden="true"><Label htmlFor="signup-website">Website</Label><Input id="signup-website" name="website" value={website} onChange={(e) => setWebsite(e.target.value)} tabIndex={-1} autoComplete="off" /></div>
        <Button type="submit" size="lg" className="w-full">{busy ? "Registrierung läuft …" : "Konto erstellen"}</Button>
      </fieldset>
    </form>}
    <p><Link className="link" to="/login">Zur Anmeldung</Link></p>
  </SignupCard>;
}

export function SignupConfirmPage({ onComplete = () => window.location.replace("/") }: { onComplete?: () => void }) {
  const deployment = useDeploymentMode();
  const [params, setParams] = useSearchParams();
  const [token] = useState(() => params.get("token") ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const completed = useRef(false);

  useEffect(() => {
    // The token stays only in this component's memory until the user submits.
    if (params.has("token")) setParams({}, { replace: true });
  }, [params, setParams]);

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || completed.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/signup/confirm", { token, password });
      completed.current = true;
      setPassword("");
      onComplete();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Bestätigung derzeit nicht verfügbar. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (deployment.mode === null && !deployment.error) return <main className="p-6" role="status">Lade...</main>;
  if (deployment.mode !== "hosted") return <Navigate to="/login" replace />;
  return <SignupCard title="E-Mail bestätigen" description="Geben Sie das bei der Registrierung gewählte Passwort ein, um Ihre E-Mail-Adresse zu bestätigen.">
    <form onSubmit={confirm} className="space-y-4" aria-busy={busy} aria-describedby={error ? "signup-confirm-error" : undefined}>
      {error && <p id="signup-confirm-error" role="alert" className="break-words text-destructive">{error}</p>}
      <div className="space-y-2"><Label htmlFor="signup-confirm-password">Passwort</Label>
        <Input id="signup-confirm-password" className="h-11" type="password" value={password} onChange={(e) => setPassword(e.target.value)} maxLength={128} required autoComplete="current-password" disabled={busy} /></div>
      <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Bestätigung läuft …" : "E-Mail bestätigen"}</Button>
    </form>
    <Link to="/signup" className="link">Zur Registrierung</Link>
  </SignupCard>;
}
