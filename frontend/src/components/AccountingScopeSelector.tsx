import { useState } from "react";
import { SearchChoice } from "./SearchChoice";

interface ScopeChoice {
  id: string; label: string; scope_type: string; valid_from: string; valid_to: string | null;
}

/** Explicit financial context; the server filters by the required live capability. */
export function AccountingScopeSelector({ propertyId, value, onChange, kind, capability = "prepare" }: {
  propertyId?: string; value?: string | null; onChange: (id: string | null) => void;
  kind?: "WEG" | "MIET"; capability?: "prepare" | "read";
}) {
  const [selected, setSelected] = useState<ScopeChoice | null>(null);
  if (!propertyId && capability === "prepare") return null;
  const query = new URLSearchParams({ capability, financial_creation: "true" });
  if (propertyId) query.set("property_id", propertyId);
  if (kind) query.set("scope_type", kind);
  return <SearchChoice<ScopeChoice>
    endpoint={`/api/accounting-scopes/page?${query}`}
    label="Abrechnungskreis"
    value={selected?.id === value ? selected : null}
    onChange={item => { setSelected(item); onChange(item?.id ?? null); }}
    display={item => `${item.label} · ${item.scope_type} · ${item.valid_from} – ${item.valid_to || "offen"}`}
  />;
}
