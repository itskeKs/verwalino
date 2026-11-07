/**
 * Login page -- POST /api/auth/login with email + password.
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
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
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
