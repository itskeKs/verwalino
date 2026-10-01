import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Property } from "../api/types";
import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "./ListSearch";

/** A bounded search preserves an existing selection outside the current page. */
export function PropertySelector({ value, onChange, label, required = false, emptyLabel = "Alle Objekte" }: {
  value: string; onChange: (id: string) => void; label: string; required?: boolean; emptyLabel?: string;
}) {
  const page = usePagedList<Property>("/api/properties/page");
  const [selected, setSelected] = useState<Property | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  useEffect(() => {
    if (!value) return;
    const controller = new AbortController();
    api.get<Property>(`/api/properties/${encodeURIComponent(value)}`, { signal: controller.signal })
      .then(item => { if (!controller.signal.aborted) { setSelected(item); setSelectionError(null); } })
      .catch(error => { if (!controller.signal.aborted) setSelectionError(String(error)); });
    return () => controller.abort();
  }, [value]);
  const options = selected?.id === value && !page.items.some(item => item.id === value)
    ? [selected, ...page.items] : page.items;
  return <fieldset style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
    <legend>{label}</legend>
    <label style={{ display: "block" }}>Objekte durchsuchen
      <input type="search" maxLength={100} value={page.query} onChange={event => page.setQuery(event.target.value)} />
    </label>
    <select aria-label={label} required={required} value={value} onChange={event => onChange(event.target.value)}>
      <option value="">{emptyLabel}</option>
      {value && !options.some(item => item.id === value) && <option value={value} disabled>Auswahl wird geladen…</option>}
      {options.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
    {(page.error || (value && selectionError)) && <p className="error-text">{page.error || selectionError}</p>}
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </fieldset>;
}
