import { useEffect, useState } from "react";
import { api } from "../api/client";
import { SearchChoice } from "./SearchChoice";

interface ScopeChoice {
  id: string; property_id?: string; label: string; scope_type: string; valid_from: string; valid_to: string | null;
}

/** Explicit financial context; the server filters by the required live capability. */
export function AccountingScopeSelector({ propertyId, value, onChange, kind, capability = "prepare", onValidityChange }: {
  propertyId?: string; value?: string | null; onChange: (id: string | null) => void;
  onValidityChange?: (valid: boolean) => void;
  kind?: "WEG" | "MIET"; capability?: "prepare" | "read";
}) {
  const [selected, setSelected] = useState<ScopeChoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const matches = selected?.id === value && (!propertyId || selected?.property_id === propertyId)
    && (!kind || selected?.scope_type === kind);
  const valid = !value || (matches && (!error) && (capability !== "prepare" || !!propertyId));
  useEffect(() => { onValidityChange?.(valid); }, [valid, onValidityChange]);
  useEffect(() => {
    if (!value || matches || (capability === "prepare" && !propertyId)) return;
    let current = true;
    api.get<ScopeChoice>(`/api/accounting-scopes/${encodeURIComponent(value)}?capability=${capability}`).then(item => {
      if (!current) return;
      if ((propertyId && item.property_id !== propertyId) || (kind && item.scope_type !== kind)) {
        setError("Der übernommene Abrechnungskreis passt nicht zur Auswahl. Bitte neu wählen.");
        return;
      }
      setSelected(item);
      setError(null);
    }).catch(() => { if (current) setError("Abrechnungskreis nicht verfügbar. Bitte die Berechtigung prüfen oder neu wählen."); });
    return () => { current = false; };
  }, [value, matches, propertyId, kind, capability]);
  if (!propertyId && capability === "prepare") return null;
  const query = new URLSearchParams({ capability, financial_creation: "true" });
  if (propertyId) query.set("property_id", propertyId);
  if (kind) query.set("scope_type", kind);
  return <div className="min-w-0"><SearchChoice<ScopeChoice>
    endpoint={`/api/accounting-scopes/page?${query}`}
    label="Abrechnungskreis"
    value={value ? (matches && selected ? selected : {
      id: value, label: error ? "Übernommener Kreis nicht verfügbar – bitte neu wählen" : "Abrechnungskreis wird geprüft…",
      scope_type: "", valid_from: "", valid_to: null,
    }) : null}
    onChange={item => { setError(null); setSelected(item); onChange(item?.id ?? null); }}
    display={item => !item.valid_from ? item.label : `${item.label} · ${item.scope_type} · ${item.valid_from} – ${item.valid_to || "offen"}`}
  />{error && <p role="alert" className="error-text">{error}</p>}</div>;
}
