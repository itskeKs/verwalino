/**
 * Second-factor enrolment flow (issue #208): QR code and typed secret,
 * confirmation with one code, then the ten one-time recovery codes.
 * Shared by the settings section and the hosted enrolment wizard.
 */

import { type FormEvent, useState } from "react";
import QRCode from "qrcode";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

type Phase =
  | { kind: "intro" }
  | { kind: "pending"; secret: string; otpauthUri: string; qrDataUrl: string }
  | { kind: "confirmed"; recoveryCodes: string[] };

export function SecondFactorEnrolment({ onConfirmed }: { onConfirmed?: () => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: "intro" });
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setError(null);
    setBusy(true);
    try {
      const enrolment = await endpoints.startSecondFactorEnrolment();
      const qrDataUrl = await QRCode.toDataURL(enrolment.otpauth_uri, { width: 220, margin: 1 });
      setPhase({ kind: "pending", secret: enrolment.secret, otpauthUri: enrolment.otpauth_uri, qrDataUrl });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Einrichtung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (phase.kind !== "pending") return;
    setError(null);
    setBusy(true);
    try {
      const answer = await endpoints.confirmSecondFactorEnrolment(code.replace(/\s/g, ""));
      setPhase({ kind: "confirmed", recoveryCodes: answer.recovery_codes });
      onConfirmed?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bestätigung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  }

  if (phase.kind === "confirmed") {
    return (
      <div className="second-factor-recovery" data-testid="second-factor-recovery">
        <p role="status">Der zweite Faktor ist eingerichtet. Bitte speichern Sie die zehn Wiederherstellungscodes jetzt ab — sie werden nicht erneut angezeigt.</p>
        <ol className="recovery-codes">
          {phase.recoveryCodes.map(item => <li key={item}><code>{item}</code></li>)}
        </ol>
        <div className="theme-save-row">
          <Button variant="outline" onClick={() => window.print()}>Wiederherstellungscodes drucken</Button>
          <Button onClick={() => { setPhase({ kind: "intro" }); setCode(""); }}>Fertig</Button>
        </div>
      </div>
    );
  }

  if (phase.kind === "pending") {
    return (
      <form onSubmit={confirm} aria-busy={busy}>
        {error && <p role="alert" className="error-text">{error}</p>}
        <p>Scannen Sie den QR-Code mit Ihrer Authenticator-App, oder geben Sie den Schlüssel manuell ein. Bestätigen Sie danach mit dem aktuellen 6-stelligen Code.</p>
        <div className="second-factor-enrolment">
          <img src={phase.qrDataUrl} width={220} height={220} alt="QR-Code für die Authenticator-App" />
          <div>
            <p className="section-label">Schlüssel manuell eingeben</p>
            <p><code className="second-factor-secret">{phase.secret}</code></p>
          </div>
        </div>
        <fieldset disabled={busy} className="min-w-0 space-y-5">
          <div className="public-auth-field">
            <Label htmlFor="second-factor-confirm-code">Aktueller 6-stelliger Code</Label>
            <Input id="second-factor-confirm-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={6} value={code} onChange={(event) => setCode(event.target.value)} required autoFocus />
          </div>
          <Button type="submit" disabled={busy}>{busy ? "Wird geprüft …" : "Zweiten Faktor bestätigen"}</Button>
        </fieldset>
      </form>
    );
  }

  return (
    <div>
      {error && <p role="alert" className="error-text">{error}</p>}
      <p>Schützt Ihr Benutzerkonto mit einem Code aus einer Authenticator-App zusätzlich zum Passwort.</p>
      <Button onClick={() => void start()} disabled={busy}>{busy ? "Wird vorbereitet …" : "Zweiten Faktor einrichten"}</Button>
    </div>
  );
}

