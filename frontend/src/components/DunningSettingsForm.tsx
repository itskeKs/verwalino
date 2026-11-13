import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { getDunningSettings, saveDunningSettings } from "../api/endpoints";
import type { DunningSettings } from "../api/types";

const FEES = ["fee_level_1", "fee_level_2", "fee_level_3"] as const;

// Documented constant (DESIGN.md): show the notice from this many days before
// the stored statutory base-rate table ends until a release extends it.
const BASE_RATE_NOTICE_DAYS = 60;

function daysUntil(date: string): number {
  const [year, month, day] = date.split("-").map(Number);
  const target = Date.UTC(year, month - 1, day);
  const now = new Date();
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86_400_000);
}

function formatGermanDate(date: string): string {
  const [year, month, day] = date.split("-");
  return `${day}.${month}.${year}`;
}

export function DunningSettingsForm({ editable }: { editable: boolean }) {
  const [settings, setSettings] = useState<DunningSettings | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    getDunningSettings().then(value => {
      if (active) setSettings(value);
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { active = false; };
  }, [revision]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editable || !settings || saving) return;

    setError("");
    setMessage("");
    const body = { default_interest_enabled: settings.default_interest_enabled } as
      Pick<DunningSettings, "fee_level_1" | "fee_level_2" | "fee_level_3" | "default_interest_enabled">;
    for (const fee of FEES) {
      const value = settings[fee].trim().replace(",", ".");
      if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(value)) {
        setError("Bitte für jede Mahnstufe einen Betrag ab 0,00 € mit höchstens zwei Nachkommastellen eingeben.");
        return;
      }
      body[fee] = value;
    }

    setSaving(true);
    try {
      setSettings({
        ...(await saveDunningSettings(body)),
        base_rate_covered_until: settings.base_rate_covered_until,
      });
      setMessage("Mahneinstellungen gespeichert.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="card mb-4">
      <summary className="cursor-pointer font-semibold">Mahneinstellungen</summary>
      {!settings && !error && <p role="status">Mahneinstellungen werden geladen…</p>}
      {settings && (
        <form onSubmit={save} className="mt-4">
          {settings.base_rate_covered_until !== null
            && daysUntil(settings.base_rate_covered_until) <= BASE_RATE_NOTICE_DAYS && (
            <p className="muted">
              Der amtliche Basiszinssatz ist nur bis {formatGermanDate(settings.base_rate_covered_until)}{" "}
              hinterlegt. Mahnungen mit Verzugszinsen sind danach erst mit dem nächsten Update möglich.
            </p>
          )}
          <fieldset disabled={saving} className="form-grid min-w-0 border-0 p-0">
            {FEES.map((fee, index) => (
              <label key={fee}>
                Mahngebühr Stufe {index + 1} (EUR)
                <input inputMode="decimal" required readOnly={!editable}
                  value={settings[fee].replace(".", ",")}
                  onChange={event => {
                    setMessage("");
                    setSettings({ ...settings, [fee]: event.target.value });
                  }} />
              </label>
            ))}
            <label className="form-full-width !flex-row items-center gap-2">
              <input type="checkbox" className="!w-auto" checked={settings.default_interest_enabled}
                disabled={!editable}
                onChange={event => {
                  setMessage("");
                  setSettings({ ...settings, default_interest_enabled: event.target.checked });
                }} />
              Verzugszinsen automatisch berechnen
            </label>
          </fieldset>
          <p className="muted">Die Einstellungen gelten für neue Einzelmahnungen und Mahnläufe. Die Voreinstellung für Stufe 1 beträgt {"0,00\u00a0€"}.</p>
          {editable && <button className="btn btn--primary mt-2" disabled={saving}>Mahneinstellungen speichern</button>}
        </form>
      )}
      {error && <p role="alert">{error}</p>}
      {!settings && error && <button className="btn" onClick={() => {
        setError("");
        setRevision(value => value + 1);
      }}>Erneut laden</button>}
      {message && <p role="status" className="mt-2">{message}</p>}
    </details>
  );
}
