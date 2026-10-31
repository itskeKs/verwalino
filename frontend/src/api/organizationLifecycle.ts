export interface OrganizationLifecycle {
  id: string;
  name: string;
  import_available: boolean;
  deletion: { status: string; execute_after: string | null } | null;
}

export function lifecycleChanged() { window.dispatchEvent(new Event("organization-lifecycle-changed")); }
