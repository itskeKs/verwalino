import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";

interface UserRow { id: string; display_name: string; email: string; is_active: boolean }

function UserList() {
  const page = usePagedList<UserRow>("/api/users/page");
  return <>
    <ListSearch label="Benutzer suchen" onSearch={page.setQuery} />
    {page.error && <p className="error-text">{page.error}</p>}
    {page.loading && page.items.length === 0 ? <p>Lade…</p> : <div className="card paged-table">
      <table className="data-table"><thead><tr><th>Name</th><th>E-Mail</th><th>Status</th></tr></thead>
        <tbody>{page.items.map(user => <tr key={user.id}><td>{user.display_name || "–"}</td><td>{user.email}</td><td>{user.is_active ? "Aktiv" : "Deaktiviert"}</td></tr>)}</tbody>
      </table>
      {page.items.length === 0 && <p>Keine Benutzer gefunden.</p>}
    </div>}
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

export function UsersPage() {
  const { session } = useAuth();
  return <div className="paged-list"><h1>Benutzer</h1>{session?.role === "OWNER_ADMIN"
    ? <><p><Link to="/berechtigungen">Bereiche und Teamrechte verwalten</Link></p><UserList /></> : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>}</div>;
}
