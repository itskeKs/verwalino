import { useState } from "react";

export function ListSearch({ label, onSearch }: { label: string; onSearch: (query: string) => void }) {
  const [value, setValue] = useState("");
  return <form role="search" onSubmit={event => { event.preventDefault(); onSearch(value.trim()); }}
    style={{ display: "flex", gap: "0.5rem", alignItems: "end", flexWrap: "wrap", marginBottom: "1rem" }}>
    <label>{label}<input type="search" value={value} maxLength={100} onChange={event => setValue(event.target.value)} /></label>
    <button className="btn" type="submit">Suchen</button>
  </form>;
}

export function MoreResults({ available, loading, onMore }: { available: boolean; loading: boolean; onMore: () => void }) {
  return available ? <button type="button" className="btn" disabled={loading} onClick={onMore} style={{ marginTop: "1rem" }}>
    {loading ? "Lade…" : "Weitere laden"}
  </button> : null;
}
