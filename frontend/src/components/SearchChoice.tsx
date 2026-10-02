import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "./ListSearch";

/** Bounded setup selector. The selected object survives subsequent searches. */
export function SearchChoice<T extends { id: string }>({ endpoint, value, onChange, label, display, required = false }: {
  endpoint: string; value: T | null; onChange: (value: T | null) => void;
  label: string; display: (value: T) => string; required?: boolean;
}) {
  const page = usePagedList<T>(endpoint);
  const choices = value && !page.items.some(item => item.id === value.id) ? [value, ...page.items] : page.items;
  return <fieldset style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
    <legend>{label}</legend>
    <input className="mb-1 w-full min-w-0" aria-label={`${label} suchen`} placeholder="Suchen…" type="search" maxLength={100} value={page.query} onChange={e => page.setQuery(e.target.value)} />
    <select className="w-full min-w-0" aria-label={label} required={required} value={value?.id ?? ""} onChange={e => onChange(choices.find(item => item.id === e.target.value) ?? null)}>
      <option value="">Bitte wählen</option>
      {choices.map(item => <option key={item.id} value={item.id}>{display(item)}</option>)}
    </select>
    {page.error && <p role="alert" className="error-text">{page.error}</p>}
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </fieldset>;
}
