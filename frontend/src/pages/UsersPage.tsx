import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { createUser } from "../api/endpoints";

interface UserRow { id: string; display_name: string; email: string; is_active: boolean }

function CreateUserForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [role, setRole] = React.useState("CLERK");
  const [error, setError] = React.useState("");
  const [creating, setCreating] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!email || !password) { setError("E-Mail und Passwort sind erforderlich."); return; }
    setCreating(true);
    try {
      await createUser(email, password, displayName, role);
      setEmail(""); setPassword(""); setDisplayName(""); setRole("CLERK");
      onSuccess();
    } catch (err: unknown) {
      if (err instanceof Error && "response" in err) {
        const body = (err as any).response?.data;
        setError(typeof body?.detail === "string" ? body.detail : "Erstellung fehlgeschlagen");
      } else {
        setError(String(err));
      }
    } finally {
      setCreating(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card" style={{ marginBottom: "1rem" }}>
      <h3>Neuen Benutzer anlegen</h3>
      {error && <p className="error-text">{error}</p>}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
        <input type="email" placeholder="E-Mail *" value={email} onChange={e => setEmail(e.target.value)} required />
        <input type="text" placeholder="Name" value={displayName} onChange={e => setDisplayName(e.target.value)} />
        <input type="password" placeholder="Startpasswort *" value={password} onChange={e => setPassword(e.target.value)} required />
        <select value={role} onChange={e => setRole(e.target.value)}>
          <option value="CLERK">Sachbearbeiter</option>
          <option value="READ_ONLY">Lesender Benutzer</option>
          <option value="OWNER_ADMIN">Inhaber/Admin</option>
        </select>
      </div>
      <button type="submit" disabled={creating} style={{ marginTop: "0.5rem" }}>
        {creating ? "Erstelle…" : "Benutzer anlegen"}
      </button>
    </form>
  );
}

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
    ? <><p><Link to="/freigaben-vorbereiten">Bestand und Freigaben vorbereiten</Link> · <Link to="/berechtigungen">Bereiche und Teamrechte verwalten</Link> · <Link to="/gebaeudedaten-rechte">Gemeinsame Gebäudedaten vorbereiten</Link> · <Link to="/kontaktdaten-rechte">Kontaktdaten-Rechte vorbereiten</Link></p><CreateUserForm onSuccess={() => window.location.reload()} /><UserList /></> : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>}</div>;
}
