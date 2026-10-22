import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

interface DeleteProps {
  entityId: string;
  title: string;
  entityName: string;
  remove: (id: string, signal?: AbortSignal) => Promise<void>;
  onDeleted: () => void;
}

export function MasterDataDelete(props: DeleteProps) {
  const { session } = useAuth();
  const location = useLocation();
  const [sessionScope, setSessionScope] = useState({ session, generation: 0 });
  if (sessionScope.session !== session) {
    setSessionScope({ session, generation: sessionScope.generation + 1 });
  }
  if (session?.role !== "OWNER_ADMIN" && session?.role !== "CLERK") return null;
  // A different entity, route or session must never inherit an open confirmation.
  const scope = JSON.stringify([props.entityId, location.key, sessionScope.generation, session.user_id, session.organization_id, session.role]);
  return <DeleteConfirmation key={scope} {...props} />;
}

function DeleteConfirmation({ entityId, title, entityName, remove, onDeleted }: DeleteProps) {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const liveSession = useRef(session);
  useEffect(() => {
    liveSession.current = session;
    const current = ++generation.current;
    return () => { generation.current = current + 1; controller.current?.abort(); };
  }, [session]);

  async function confirm() {
    if (lock.current || liveSession.current !== session || (session?.role !== "OWNER_ADMIN" && session?.role !== "CLERK")) return;
    lock.current = true;
    const current = generation.current;
    const request = new AbortController();
    controller.current = request;
    setBusy(true); setError(null);
    try {
      await remove(entityId, request.signal);
      if (generation.current !== current || request.signal.aborted) return;
      setOpen(false);
      onDeleted();
    } catch (err) {
      if (generation.current === current && !request.signal.aborted) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (generation.current === current) { lock.current = false; setBusy(false); }
    }
  }

  return <>
    <button type="button" className="btn btn--small" disabled={busy} onClick={() => { setError(null); setOpen(true); }}>{title}</button>
    {!open && error && <p role="alert" className="error-text">{error}</p>}
    <Dialog open={open} onOpenChange={value => { if (!lock.current) setOpen(value); }}>
      <DialogContent onEscapeKeyDown={event => { if (lock.current) event.preventDefault(); }} onPointerDownOutside={event => { if (lock.current) event.preventDefault(); }}>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>„{entityName}“ unwiderruflich löschen? Bestehende Referenzen oder Quellschutz können das Löschen verhindern. Verknüpfte Daten werden nicht mitgelöscht.</DialogDescription>
        {error && <p role="alert" className="error-text">{error}</p>}
        {busy && <p role="status">Löschung läuft…</p>}
        <button type="button" className="btn btn--primary" disabled={busy} onClick={() => { void confirm(); }}>Löschen bestätigen</button>
        <button type="button" className="btn" disabled={busy} onClick={() => setOpen(false)}>Abbrechen</button>
      </DialogContent>
    </Dialog>
  </>;
}
