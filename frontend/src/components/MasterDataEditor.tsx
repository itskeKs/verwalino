import { useCallback, useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

export interface MasterDataField<T> {
  key: Extract<keyof T, string>;
  label: string;
  maxLength?: number;
  nullable?: boolean;
  type?: "date";
  options?: { value: string; label: string }[];
}

/** Only changed fields cross the API boundary; financial identities and scope IDs are never included. */
export function MasterDataEditor<T extends object>({ title, initial, fields, save, onSaved, active, setActive, endLabel, confirmChanges, description }: {
  title: string;
  initial: T;
  fields: MasterDataField<T>[];
  save: (changes: Partial<T>) => Promise<unknown>;
  onSaved: () => void;
  active?: boolean;
  setActive?: (active: boolean) => Promise<unknown>;
  endLabel?: string;
  confirmChanges?: (changes: Partial<T>) => string | null;
  description?: string;
}) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const invalidateGeneration = useCallback(() => { generation.current++; }, []);
  useEffect(() => { invalidateGeneration(); return invalidateGeneration; }, [invalidateGeneration]);

  async function mutate(action: () => Promise<unknown>) {
    if (busyRef.current) return;
    busyRef.current = true;
    const current = generation.current;
    setBusy(true); setError(null);
    try {
      await action();
      if (generation.current !== current) return;
      setOpen(false);
      onSaved();
    } catch (err) {
      if (generation.current === current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (generation.current === current) { busyRef.current = false; setBusy(false); }
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busyRef.current) return;
    let changes: Partial<T> = {};
    for (const field of fields) {
      const value = values[field.key] ?? "";
      if (value !== String(initial[field.key] ?? "")) changes = { ...changes, [field.key]: field.nullable && value === "" ? null : value };
    }
    if (!Object.keys(changes).length) { setOpen(false); return; }
    const confirmation = confirmChanges?.(changes);
    if (confirmation && !window.confirm(confirmation)) return;
    void mutate(() => save(changes));
  }

  return <>
    <button type="button" className="btn btn--small" disabled={busy} onClick={() => {
      setValues(Object.fromEntries(fields.map(field => [field.key, String(initial[field.key] ?? "")])));
      setError(null); setOpen(true);
    }}>{title}</button>
    {setActive && endLabel && <button type="button" className="btn btn--small" disabled={busy} onClick={() => {
      const label = active !== false ? endLabel : endLabel.replace("beenden", "reaktivieren");
      if (!busyRef.current && window.confirm(`${label}? Historische Daten bleiben erhalten.`)) void mutate(() => setActive(active === false));
    }}>{active !== false ? endLabel : endLabel.replace("beenden", "reaktivieren")}</button>}
    {!open && error && <p role="alert" className="error-text">{error}</p>}
    <Dialog open={open} onOpenChange={value => { if (!busyRef.current) setOpen(value); }}>
      <DialogContent onEscapeKeyDown={event => { if (busyRef.current) event.preventDefault(); }} onPointerDownOutside={event => { if (busyRef.current) event.preventDefault(); }}>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description ?? "Stammdaten ändern. Historische Buchungen bleiben erhalten."}</DialogDescription>
        <form onSubmit={submit} className="form-grid">
          <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0, display: "contents" }}>
            {fields.map(field => <label key={field.key}>{field.label}
              {field.options ? <select aria-label={field.label} value={values[field.key] ?? ""} onChange={event => setValues({ ...values, [field.key]: event.target.value })}>
                {field.options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select> : <input type={field.type ?? "text"} maxLength={field.maxLength} value={values[field.key] ?? ""} onChange={event => setValues({ ...values, [field.key]: event.target.value })} />}
            </label>)}
            {error && <p role="alert" className="error-text">{error}</p>}
            <div className="form-actions">
              <button type="submit" className="btn btn--primary">{busy ? "Speichern…" : "Änderungen speichern"}</button>
              <button type="button" className="btn" onClick={() => setOpen(false)}>Abbrechen</button>
            </div>
          </fieldset>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
