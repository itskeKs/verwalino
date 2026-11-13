import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useDeploymentMode } from "../auth/useDeploymentMode";
import { Button } from "../components/ui/button";
import { PublicAuthLayout, PublicAuthMessage } from "../components/PublicAuthLayout";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

const CONFIRM_TOKEN_KEY = "verwalino.signup-confirm-token";

export function SignupPage() {
  const deployment = useDeploymentMode();
  const [organization, setOrganization] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [website, setWebsite] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [avAccepted, setAvAccepted] = useState(false);
  const [assentError, setAssentError] = useState<string | null>(null);
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
    const legal = deployment.legalDocuments;
    if (!receipt && legal && (!termsAccepted || !avAccepted)) {
      setAssentError("Bitte bestätigen Sie die Nutzungsbedingungen und den Auftragsverarbeitungsvertrag.");
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const answer = await api.post<{ message: string }>(receipt ? "/api/signup/resend" : "/api/signup", receipt ? { email, website } : {
        organization_name: organization, admin_name: name, email, password, website,
        terms_version: legal?.terms.version ?? "", av_version: legal?.av_vertrag.version ?? "",
      });
      setReceipt(answer.message);
      setPassword(""); setRepeat("");
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "legal_assent_refused") {
        setAssentError(failure.message);
      } else {
        setError(failure instanceof ApiError ? failure.message : "Registrierung fehlgeschlagen. Bitte versuchen Sie es erneut.");
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (deployment.mode === null && !deployment.error) return <main className="p-6" role="status">Lade...</main>;
  if (deployment.mode !== "hosted") return <Navigate to="/login" replace />;
  const legalDocuments = deployment.legalDocuments;
  return <PublicAuthLayout title="Konto erstellen" description="Registrieren Sie Ihre Organisation. Bestätigen Sie anschließend Ihre E-Mail-Adresse über den Link in der E-Mail.">
    {error && <PublicAuthMessage id="signup-error">{error}</PublicAuthMessage>}
    {receipt ? <>
      <PublicAuthMessage role="status">{receipt}</PublicAuthMessage>
      <p className="public-auth-note">Bitte prüfen Sie auch Ihren Spam-Ordner. Ein neuer Link kann frühestens nach 10 Minuten gesendet werden.</p>
      <Button variant="outline" className="w-full" disabled={busy} onClick={() => void submit()}>{busy ? "Anfrage läuft …" : "Erneut senden"}</Button>
    </> : <form onSubmit={submit} aria-busy={busy} aria-describedby={error ? "signup-error" : undefined}>
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        <div className="public-auth-field"><Label htmlFor="signup-org">Organisation</Label><Input id="signup-org" className="h-11" value={organization} onChange={(e) => setOrganization(e.target.value)} maxLength={255} required autoComplete="organization" /></div>
        <div className="public-auth-field"><Label htmlFor="signup-name">Name</Label><Input id="signup-name" className="h-11" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} required autoComplete="name" /></div>
        <div className="public-auth-field"><Label htmlFor="signup-email">E-Mail</Label><Input id="signup-email" className="h-11" type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} required autoComplete="email" /></div>
        <div className="public-auth-field"><Label htmlFor="signup-password">Passwort</Label><Input id="signup-password" className="h-11" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" aria-describedby="signup-password-help" /><p id="signup-password-help" className="text-sm text-muted-foreground">Verwenden Sie 12 bis 128 Zeichen.</p></div>
        <div className="public-auth-field"><Label htmlFor="signup-repeat">Passwort wiederholen</Label><Input id="signup-repeat" className="h-11" type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} minLength={12} maxLength={128} required autoComplete="new-password" /></div>
        <div className="hidden" aria-hidden="true"><Label htmlFor="signup-website">Website</Label><Input id="signup-website" name="website" value={website} onChange={(e) => setWebsite(e.target.value)} tabIndex={-1} autoComplete="off" /></div>
        {legalDocuments && <div className="public-auth-field space-y-2" data-testid="signup-assent">
          {([["terms", termsAccepted, setTermsAccepted, "Nutzungsbedingungen", "signup-terms"],
             ["av_vertrag", avAccepted, setAvAccepted, "Auftragsverarbeitungsvertrag", "signup-av"]] as const).map(([key, checked, setChecked, label, id]) => (
            <div key={key} className="flex items-start gap-2">
              <input id={id} type="checkbox" className="mt-1 h-4 w-4 shrink-0"
                     checked={checked} onChange={(e) => { setAssentError(null); setChecked(e.target.checked); }} />
              <label htmlFor={id} className="text-sm leading-snug">
                Ich habe die <a className="link" href={legalDocuments[key].url} target="_blank" rel="noreferrer">{label}</a>{" "}
                (Version {legalDocuments[key].version}) gelesen und akzeptiere diese.
              </label>
            </div>
          ))}
          {assentError && <p role="alert" className="text-sm text-destructive">{assentError}</p>}
        </div>}
        {!legalDocuments && assentError && <p role="alert" className="text-sm text-destructive">{assentError}</p>}
        <Button type="submit" size="lg" className="w-full">{busy ? "Registrierung läuft …" : "Konto erstellen"}</Button>
      </fieldset>
    </form>}
    <p className="public-auth-footer"><Link className="link" to="/login">Zur Anmeldung</Link></p>
  </PublicAuthLayout>;
}

export function SignupConfirmPage({ onComplete = () => window.location.replace("/") }: { onComplete?: () => void }) {
  const deployment = useDeploymentMode();
  const [params, setParams] = useSearchParams();
  const [token, setToken] = useState(() => params.get("token") ?? sessionStorage.getItem(CONFIRM_TOKEN_KEY) ?? "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const completed = useRef(false);

  useEffect(() => {
    // Save before removing the query so reloading this tab keeps the link.
    if (params.has("token")) {
      sessionStorage.setItem(CONFIRM_TOKEN_KEY, params.get("token") ?? "");
      setParams({}, { replace: true });
    }
  }, [params, setParams]);

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current || completed.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/signup/confirm", { token, password });
      sessionStorage.removeItem(CONFIRM_TOKEN_KEY);
      setToken("");
      completed.current = true;
      setPassword("");
      onComplete();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 403) {
        sessionStorage.removeItem(CONFIRM_TOKEN_KEY);
        setToken("");
      }
      setError(failure instanceof ApiError ? failure.message : "Bestätigung derzeit nicht verfügbar. Bitte versuchen Sie es erneut.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (deployment.mode === null && !deployment.error) return <main className="p-6" role="status">Lade...</main>;
  if (deployment.mode !== "hosted") return <Navigate to="/login" replace />;
  return <PublicAuthLayout title="E-Mail bestätigen" description="Geben Sie das bei der Registrierung gewählte Passwort ein, um Ihre E-Mail-Adresse zu bestätigen.">
    <form onSubmit={confirm} aria-busy={busy} aria-describedby={error ? "signup-confirm-error" : undefined}>
      {error && <PublicAuthMessage id="signup-confirm-error">{error}</PublicAuthMessage>}
      <div className="public-auth-field"><Label htmlFor="signup-confirm-password">Passwort</Label>
        <Input id="signup-confirm-password" className="h-11" type="password" value={password} onChange={(e) => setPassword(e.target.value)} maxLength={128} required autoComplete="current-password" disabled={busy} /></div>
      <Button type="submit" size="lg" className="w-full" disabled={busy}>{busy ? "Bestätigung läuft …" : "E-Mail bestätigen"}</Button>
    </form>
    <p className="public-auth-footer"><Link to="/signup" className="link">Zur Registrierung</Link></p>
  </PublicAuthLayout>;
}
