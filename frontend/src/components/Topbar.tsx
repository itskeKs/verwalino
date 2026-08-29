/**
 * Top bar (UI-Kit) -- hamburger (mobile), spacer, role chip + logout.
 */

import { Menu, LogOut } from "lucide-react";
import { useAuth } from "../auth/useAuth";
import { Button } from "./ui/button";

interface Props {
  onToggleSidebar: () => void;
}

export function Topbar({ onToggleSidebar }: Props) {
  const { session, logout } = useAuth();

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 md:px-8">
      <button
        className="flex h-9 w-9 items-center justify-center rounded-md text-foreground hover:bg-accent md:hidden"
        onClick={onToggleSidebar}
        aria-label="Navigation umschalten"
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="flex-1" />

      {session && (
        <div className="flex items-center gap-3">
          <span className="hidden rounded-full bg-secondary px-3 py-1 text-xs font-medium text-secondary-foreground sm:inline">
            {roleLabelDe(session.role)}
          </span>
          <Button variant="ghost" size="sm" onClick={logout}>
            <LogOut className="h-4 w-4" />
            Abmelden
          </Button>
        </div>
      )}
    </header>
  );
}

function roleLabelDe(role: string): string {
  switch (role) {
    case "OWNER_ADMIN":
      return "Administrator";
    case "CLERK":
      return "Sachbearbeiter:in";
    case "READ_ONLY":
      return "Nur-Lesen";
    default:
      return role;
  }
}
