import { type FormEvent, useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useSetupStatus } from "../auth/useSetupStatus";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function SetupStatusError({ retry }: { retry: () => void }) {
  return <main className="mx-auto max-w-lg space-y-4 p-6">
    <p role="alert">Der Einrichtungsstatus konnte nicht geladen werden. Bitte versuchen Sie es erneut.</p>
    <Button onClick={retry} size="lg">Erneut versuchen</Button>
  </main>;
}

export function SetupPage({ onComplete = () => window.location.replace("/"), onClosed }: { onComplete?: () => void; onClosed?: () => void }) {
  const status = useSetupStatus();
  const navigate = useNavigate();
  const [organization, setOrganization] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status.required === false) onClosed?.();
  }, [status.required, onClosed]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError(null);
    if (password !== repeat) {
      setError("Die Passwörter stimmen nicht überein. Bitte prüfen Sie Ihre Eingabe.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/setup", { token, organization_name: organization, admin_email: email, admin_name: name, password });
      // Reload from the HttpOnly session cookie; no secret enters browser storage.
      onComplete();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) {
        onClosed?.();
        navigate("/login", { replace: true });
      } else {
        setError(failure instanceof ApiError ? failure.message : "Einrichtung fehlgeschlagen. Bitte versuchen Sie es erneut.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (status.error) return <SetupStatusError retry={status.retry} />;
  if (status.required === null) return <main className="p-6" role="status">Einrichtungsstatus wird geladen …</main>;
  if (!status.required) return <Navigate to="/login" replace />;

  return <main className="flex min-h-screen items-center justify-center bg-background p-4 sm:p-6">
    <Card className="w-full min-w-0 max-w-lg">
      <CardHeader>
        <h1 className="break-words text-2xl font-semibold">Verwalino einrichten</h1>
        <CardDescription>Erstellen Sie Ihre Organisation und Ihren Administratorzugang. Den Einrichtungs-Token finden Sie im Terminal nach dem Start.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit} aria-busy={busy} aria-describedby={error ? "setup-error" : undefined}>
          {error && <p id="setup-error" role="alert" className="break-words text-sm text-destructive">{error}</p>}
          <div className="space-y-2"><Label htmlFor="setup-org">Organisation</Label>
            <Input id="setup-org" value={organization} onChange={(e) => setOrganization(e.target.value)} maxLength={255} required autoComplete="organization" className="h-11" /></div>
          <div className="space-y-2"><Label htmlFor="setup-name">Name</Label>
            <Input id="setup-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} required autoComplete="name" className="h-11" /></div>
          <div className="space-y-2"><Label htmlFor="setup-email">E-Mail</Label>
            <Input id="setup-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={320} required autoComplete="email" className="h-11" /></div>
          <div className="space-y-2"><Label htmlFor="setup-password">Passwort</Label>
            <Input id="setup-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" aria-describedby="setup-password-help" className="h-11" />
            <p id="setup-password-help" className="text-sm text-muted-foreground">Verwenden Sie 12 bis 128 Zeichen.</p></div>
          <div className="space-y-2"><Label htmlFor="setup-repeat">Passwort wiederholen</Label>
            <Input id="setup-repeat" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" className="h-11" /></div>
          <div className="space-y-2"><Label htmlFor="setup-token">Einrichtungs-Token</Label>
            <Input id="setup-token" type="password" value={token} onChange={(e) => setToken(e.target.value)} maxLength={256} required autoComplete="off" spellCheck={false} aria-describedby="setup-token-help" className="h-11" />
            <p id="setup-token-help" className="text-sm text-muted-foreground">Der Token gilt einmalig für 24 Stunden. Ein erneuter Start ersetzt einen noch offenen Token.</p></div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Einrichtung läuft …" : "Einrichtung abschließen"}</Button>
        </form>
      </CardContent>
    </Card>
  </main>;
}
