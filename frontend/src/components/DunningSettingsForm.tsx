import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { getDunningSettings, saveDunningSettings } from "../api/endpoints";
import type { DunningSettings } from "../api/types";

const FEES = ["fee_level_1", "fee_level_2", "fee_level_3"] as const;

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
    const body = { ...settings };
    for (const fee of FEES) {
      const value = body[fee].trim().replace(",", ".");
      if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(value)) {
        setError("Bitte für jede Mahnstufe einen Betrag ab 0,00 € mit höchstens zwei Nachkommastellen eingeben.");
        return;
      }
      body[fee] = value;
    }

    setSaving(true);
    try {
      setSettings(await saveDunningSettings(body));
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
          <p className="form-hint">Die Einstellungen gelten für neue Einzelmahnungen und Mahnläufe. Die Voreinstellung für Stufe 1 beträgt {"0,00\u00a0€"}.</p>
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
