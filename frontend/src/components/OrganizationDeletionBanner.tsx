import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Button } from "./ui/button";
import { lifecycleChanged, type OrganizationLifecycle } from "../api/organizationLifecycle";

export function OrganizationDeletionBanner() {
  const { session } = useAuth();
  const [organization, setOrganization] = useState<OrganizationLifecycle | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => { void api.get<OrganizationLifecycle>("/api/organizations/me").then(setOrganization).catch(() => {}); }, []);
  useEffect(() => { refresh(); window.addEventListener("organization-lifecycle-changed", refresh); return () => window.removeEventListener("organization-lifecycle-changed", refresh); }, [refresh]);
  async function cancel() {
    setBusy(true); setError(null);
    try { await api.post("/api/organizations/deletion/cancel"); refresh(); lifecycleChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Die Löschung konnte nicht widerrufen werden."); }
    finally { setBusy(false); }
  }
  if (organization?.deletion?.status !== "cooling_off") return null;
  return <aside role="status" className="mb-5 space-y-3 rounded-lg border border-border bg-secondary p-4">
    <p>Die Löschung Ihrer Organisation ist vorgemerkt. Ihre Organisation ist schreibgeschützt. Die Löschung wird frühestens ab {new Date(organization.deletion.execute_after!).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })} durchgeführt. Bis zur Durchführung können Sie die Löschung widerrufen.</p>
    {session?.role === "OWNER_ADMIN" && <Button type="button" variant="outline" disabled={busy} onClick={() => void cancel()}>Löschung widerrufen</Button>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </aside>;
}
