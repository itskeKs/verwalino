import React from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "../components/ui/dialog";
import { createUser, resetUserPassword, updateUser } from "../api/endpoints";
import type { User, UserRole } from "../api/types";
import { OrganizationDataCard } from "../components/OrganizationDataCard";

const roles: { value: UserRole; label: string }[] = [
  { value: "CLERK", label: "Sachbearbeiter" },
  { value: "READ_ONLY", label: "Lesender Benutzer" },
  { value: "OWNER_ADMIN", label: "Inhaber/Admin" },
];
function isUserRole(role: string | null | undefined): role is UserRole {
  return roles.some(option => option.value === role);
}

function useMutationGuard() {
  const mountedRef = React.useRef(false);
  const busyRef = React.useRef(false);
  React.useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  return { mountedRef, busyRef };
}

function CreateUserForm({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [displayName, setDisplayName] = React.useState("");
  const [role, setRole] = React.useState("CLERK");
  const [error, setError] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const { mountedRef, busyRef } = useMutationGuard();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busyRef.current || !mountedRef.current) return;
    setError("");
    if (!email || password.length < 12 || password.length > 128) {
      setError("E-Mail und ein Passwort mit 12 bis 128 Zeichen sind erforderlich."); return;
    }
    busyRef.current = true;
    setCreating(true);
    try {
      await createUser(email, password, displayName, role);
      if (!mountedRef.current) return;
      setEmail(""); setPassword(""); setDisplayName(""); setRole("CLERK");
      onSuccess();
    } catch (err: unknown) {
      if (mountedRef.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setCreating(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card" style={{ marginBottom: "1rem" }}>
      <h3>Neuen Benutzer anlegen</h3>
      {error && <p role="alert" className="error-text">{error}</p>}
      <fieldset disabled={creating} style={{ border: 0, padding: 0 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" }}>
          <input type="email" placeholder="E-Mail *" value={email} onChange={e => setEmail(e.target.value)} required />
          <input type="text" placeholder="Name" value={displayName} onChange={e => setDisplayName(e.target.value)} />
          <input type="password" autoComplete="new-password" minLength={12} maxLength={128} placeholder="Startpasswort *" value={password} onChange={e => setPassword(e.target.value)} required />
          <select value={role} onChange={e => setRole(e.target.value)}>
            {roles.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
        <button type="submit" disabled={creating} style={{ marginTop: "0.5rem" }}>
          {creating ? "Erstelle…" : "Benutzer anlegen"}
        </button>
      </fieldset>
    </form>
  );
}

type UserAction = { user: User; mode: "edit" | "reset" };

function UserActionDialog({ action, actorId, onClose, onSuccess }: {
  action: UserAction; actorId: string; onClose: () => void; onSuccess: () => void;
}) {
  const { user, mode } = action;
  const self = user.id === actorId;
  const initialRole = isUserRole(user.role) ? user.role : "";
  const [role, setRole] = React.useState<UserRole | "">(initialRole);
  const [active, setActive] = React.useState(user.is_active);
  const [confirmed, setConfirmed] = React.useState(false);
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const { mountedRef, busyRef } = useMutationGuard();
  const title = mode === "edit" ? "Benutzer bearbeiten" : "Passwort zurücksetzen";
  const changed = role !== initialRole || active !== user.is_active;
  const deactivating = user.is_active && !active;
  const valid = mode === "reset" ? password.length >= 12 && password.length <= 128
    : !self && changed && (!deactivating || confirmed);

  function close() {
    if (busyRef.current) return;
    setPassword("");
    onClose();
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!mountedRef.current || busyRef.current || !valid) return;
    busyRef.current = true;
    setSaving(true);
    setError("");
    try {
      if (mode === "reset") await resetUserPassword(user.id, password);
      else {
        const patch: { role?: UserRole; is_active?: boolean } = {};
        if (role && role !== initialRole) patch.role = role;
        if (active !== user.is_active) patch.is_active = active;
        await updateUser(user.id, patch);
      }
      if (!mountedRef.current) return;
      setPassword("");
      onSuccess();
      onClose();
    } catch (err: unknown) {
      if (mountedRef.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setSaving(false);
    }
  }

  return <Dialog open onOpenChange={open => { if (!open) close(); }}>
    <DialogContent onEscapeKeyDown={event => { if (busyRef.current) event.preventDefault(); }} onInteractOutside={event => { if (busyRef.current) event.preventDefault(); }}>
      <DialogTitle>{title}</DialogTitle>
      <DialogDescription>{user.display_name || "Ohne Namen"} · {user.email}</DialogDescription>
      <form onSubmit={submit}>
        {error && <p role="alert" className="error-text">{error}</p>}
        <fieldset disabled={saving} style={{ border: 0, padding: 0 }}>
          {mode === "edit" ? <>
            <label>Rolle<select value={role} disabled={self} onChange={event => {
              const value = event.target.value;
              if (isUserRole(value)) setRole(value);
            }}>
              {!initialRole && <option value="" disabled>Keine bekannte Rolle</option>}
              {roles.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select></label>
            <label><input type="checkbox" checked={active} disabled={self} onChange={event => { setActive(event.target.checked); setConfirmed(false); }} />Aktiv</label>
            {self && <p>Die eigene Admin-Rolle und der eigene Aktivstatus können hier nicht geändert werden. Der Server verhindert Selbstherabstufung und Selbstdeaktivierung (409).</p>}
            {deactivating && !self && <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Deaktivierung bestätigen: {user.email} verliert den Zugang.</label>}
          </> : <>
            <label>Neues Passwort<input type="password" autoComplete="new-password" value={password} minLength={12} maxLength={128} required onChange={event => setPassword(event.target.value)} /></label>
            <p>12 bis 128 Zeichen. Das Passwort wird nicht angezeigt oder automatisch zugestellt.</p>
            {self && <p>Das Zurücksetzen des eigenen Passworts beendet die aktuelle Sitzung. Danach ist eine erneute Anmeldung erforderlich.</p>}
          </>}
          <button type="submit" disabled={saving || !valid}>{mode === "edit" ? "Änderungen speichern" : "Passwort zurücksetzen"}</button>
          <button type="button" disabled={saving} onClick={close}>Abbrechen</button>
        </fieldset>
      </form>
    </DialogContent>
  </Dialog>;
}

function UserAdministration({ actorId }: { actorId: string }) {
  const page = usePagedList<User>("/api/users/page");
  const [action, setAction] = React.useState<UserAction | null>(null);
  return <>
    <CreateUserForm onSuccess={page.reload} />
    <ListSearch label="Benutzer suchen" onSearch={page.setQuery} />
    {page.error && <p className="error-text">{page.error}</p>}
    {page.loading && page.items.length === 0 ? <p>Lade…</p> : <div className="card paged-table">
      <table className="data-table"><thead><tr><th>Name</th><th>E-Mail</th><th>Rolle</th><th>Status</th><th>Aktion</th></tr></thead>
        <tbody>{page.items.map(user => <tr key={user.id}>
          <td>{user.display_name || "–"}</td><td>{user.email}</td>
          <td>{roles.find(option => option.value === user.role)?.label ?? user.role ?? "–"}</td>
          <td>{user.is_active ? "Aktiv" : "Deaktiviert"}</td>
          <td><button type="button" disabled={!!action} onClick={() => setAction({ user, mode: "edit" })}>Benutzer bearbeiten</button>
            <button type="button" disabled={!!action} onClick={() => setAction({ user, mode: "reset" })}>Passwort zurücksetzen</button></td>
        </tr>)}</tbody>
      </table>
      {page.items.length === 0 && <p>Keine Benutzer gefunden.</p>}
    </div>}
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
    {action && <UserActionDialog action={action} actorId={actorId} onClose={() => setAction(null)} onSuccess={page.reload} />}
  </>;
}

export function UsersPage() {
  const { session } = useAuth();
  return <div className="paged-list"><h1>Benutzer</h1>{session?.role === "OWNER_ADMIN"
    ? <><OrganizationDataCard key={`${session.organization_id}:${session.user_id}:${session.role}`} /><p><Link to="/freigaben-vorbereiten">Bestand und Freigaben vorbereiten</Link> · <Link to="/berechtigungen">Bereiche und Teamrechte verwalten</Link> · <Link to="/gebaeudedaten-rechte">Gemeinsame Gebäudedaten vorbereiten</Link> · <Link to="/kontaktdaten-rechte">Kontaktdaten-Rechte vorbereiten</Link></p><UserAdministration key={`${session.organization_id}:${session.user_id}:${session.role}`} actorId={session.user_id} /></> : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>}</div>;
}
