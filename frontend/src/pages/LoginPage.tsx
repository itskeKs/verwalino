/**
 * Login page -- POST /api/auth/login with email + password.
 */

import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { ApiError } from "../api/client";
import "./LoginPage.css";

export function LoginPage({ signupEnabled = false }: { signupEnabled?: boolean }) {
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
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1 className="login-form__title">Hausverwaltung</h1>
        <p className="login-form__subtitle">Bitte anmelden</p>

        {error && <div className="login-form__error">{error}</div>}

        <label className="login-form__label">
          E-Mail
          <input
            type="email"
            className="login-form__input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
          />
        </label>

        <label className="login-form__label">
          Passwort
          <input
            type="password"
            className="login-form__input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </label>

        <button
          type="submit"
          className="login-form__submit"
          disabled={loading}
        >
          {loading ? "..." : "Anmelden"}
        </button>
        {signupEnabled && <Link className="link" to="/signup">Konto erstellen</Link>}
      </form>
    </div>
  );
}
