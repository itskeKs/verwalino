import { useState } from "react";
import { SearchChoice } from "./SearchChoice";

interface ScopeChoice {
  id: string; label: string; scope_type: string; valid_from: string; valid_to: string | null;
}

/** Explicit creation context; the server filters by live prepare grants. */
export function AccountingScopeSelector({ propertyId, value, onChange, kind }: {
  propertyId: string; value?: string | null; onChange: (id: string | null) => void;
  kind?: "WEG" | "MIET";
}) {
  const [selected, setSelected] = useState<ScopeChoice | null>(null);
  if (!propertyId) return null;
  const query = new URLSearchParams({ property_id: propertyId, capability: "prepare", financial_creation: "true" });
  if (kind) query.set("scope_type", kind);
  return <SearchChoice<ScopeChoice>
    endpoint={`/api/accounting-scopes/page?${query}`}
    label="Abrechnungskreis"
    value={selected?.id === value ? selected : null}
    onChange={item => { setSelected(item); onChange(item?.id ?? null); }}
    display={item => `${item.label} · ${item.scope_type} · ${item.valid_from} – ${item.valid_to || "offen"}`}
  />;
}
