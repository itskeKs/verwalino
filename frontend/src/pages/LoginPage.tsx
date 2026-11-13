/**
 * Login page -- two steps when a second factor is enrolled (issue #208):
 * password first, then the code from the authenticator app or a recovery code.
 */

import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { ApiError } from "../api/client";
import { PublicAuthLayout, PublicAuthMessage } from "../components/PublicAuthLayout";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";

export function LoginPage({ signupEnabled = false, mailAvailable = false }: { signupEnabled?: boolean; mailAvailable?: boolean }) {
  const { login, completeSecondFactor } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [recoveryCodesRemaining, setRecoveryCodesRemaining] = useState<number | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const answer = await login(email, password);
      if (answer.second_factor_required) {
        setPendingToken(answer.pending_token ?? null);
        setCode("");
        setRecoveryCode("");
        setUseRecovery(false);
        setRecoveryCodesRemaining(null);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Anmeldung fehlgeschlagen");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleSecondFactor(e: FormEvent) {
    e.preventDefault();
    if (!pendingToken) return;
    setError(null);
    setLoading(true);
    try {
      const answer = await completeSecondFactor(
        useRecovery ? { pending_token: pendingToken, recovery_code: recoveryCode } : { pending_token: pendingToken, code: code }
      );
      setRecoveryCodesRemaining(answer.recovery_codes_remaining ?? null);
    } catch (err) {
      // A wrong code or recovery code is refused exactly like a wrong password.
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Anmeldung fehlgeschlagen");
      }
    } finally {
      setLoading(false);
    }
  }

  if (pendingToken) {
    return (
      <PublicAuthLayout title="Code aus der Authenticator-App" description={`Für ${email} ist ein zweiter Faktor eingerichtet.`}>
        <form onSubmit={handleSecondFactor} aria-labelledby="public-auth-title" aria-busy={loading} aria-describedby={error ? "login-error" : undefined}>
          {error && <PublicAuthMessage id="login-error">{error}</PublicAuthMessage>}
          {recoveryCodesRemaining !== null && !error && (
            <PublicAuthMessage id="login-recovery-remaining">{`Angemeldet. Es verbleiben ${recoveryCodesRemaining} Wiederherstellungscodes.`}</PublicAuthMessage>
          )}
          <fieldset disabled={loading} className="min-w-0 space-y-5">
            {!useRecovery ? (
              <div className="public-auth-field">
                <Label htmlFor="login-second-factor-code">6-stelliger Code</Label>
                <Input id="login-second-factor-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} value={code} onChange={(event) => setCode(event.target.value)} required autoFocus />
                <p className="text-sm text-muted-foreground">Öffnen Sie Ihre Authenticator-App und geben Sie den aktuellen Code für verwalino ein.</p>
              </div>
            ) : (
              <div className="public-auth-field">
                <Label htmlFor="login-recovery-code">Wiederherstellungscode</Label>
                <Input id="login-recovery-code" autoComplete="off" value={recoveryCode} onChange={(event) => setRecoveryCode(event.target.value)} required autoFocus />
              </div>
            )}
            <button type="button" className="link public-auth-recovery" onClick={() => { setUseRecovery(value => !value); setError(null); }}>
              {useRecovery ? "Zum Authenticator-Code" : "Authenticator-App nicht zur Hand?"}
            </button>
            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? "Anmeldung läuft …" : "Anmelden"}
            </Button>
          </fieldset>
        </form>
      </PublicAuthLayout>
    );
  }

  return (
    <PublicAuthLayout title="Anmelden" description="Melden Sie sich mit Ihrem Benutzerkonto an.">
      <form onSubmit={handleSubmit} aria-labelledby="public-auth-title" aria-busy={loading} aria-describedby={error ? "login-error" : undefined}>
        {error && <PublicAuthMessage id="login-error">{error}</PublicAuthMessage>}
        <fieldset disabled={loading} className="min-w-0 space-y-5">
          <div className="public-auth-field">
            <Label htmlFor="login-email">E-Mail</Label>
            <Input id="login-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="email" autoFocus />
          </div>
          <div className="public-auth-field">
            <Label htmlFor="login-password">Passwort</Label>
            <Input id="login-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="current-password" />
          </div>
          {mailAvailable && <Link className="link public-auth-recovery" to="/passwort-vergessen">Passwort vergessen?</Link>}
          <Button type="submit" size="lg" className="w-full" disabled={loading}>
            {loading ? "Anmeldung läuft …" : "Anmelden"}
          </Button>
        </fieldset>
      </form>
      {signupEnabled && <p className="public-auth-footer"><Link className="link" to="/signup">Konto erstellen</Link></p>}
    </PublicAuthLayout>
  );
}
