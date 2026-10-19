/**
 * Top bar (UI-Kit) -- hamburger (mobile), spacer, role chip + logout.
 */

import { Menu, LogOut, Key } from "lucide-react";
import { useAuth } from "../auth/useAuth";
import { Button } from "./ui/button";
import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

interface Props {
  onToggleSidebar: () => void;
}

export function Topbar({ onToggleSidebar }: Props) {
  const { session, logout } = useAuth();
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);

  const handlePasswordChange = async () => {
    setError(null);
    setSuccess(false);

    if (newPassword !== confirmPassword) {
      setError("Neue Passwörter stimmen nicht überein.");
      return;
    }

    setLoading(true);
    try {
      const resp = await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
      });

      if (resp.status === 204) {
        setSuccess(true);
        setTimeout(() => {
          setPasswordDialogOpen(false);
          setOldPassword("");
          setNewPassword("");
          setConfirmPassword("");
          setSuccess(false);
          logout();
        }, 1500);
      } else if (resp.status === 401) {
        setError("Altes Passwort ist falsch.");
      } else if (resp.status === 422) {
        const body = await resp.json();
        setError(body.detail?.[0]?.msg || "Neues Passwort不符合 Anforderungen.");
      } else {
        setError("Unbekannter Fehler.");
      }
    } catch {
      setError("Verbindungsfehler.");
    } finally {
      setLoading(false);
    }
  };

  const resetDialog = () => {
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setError(null);
    setSuccess(false);
    setPasswordDialogOpen(false);
  };

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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setPasswordDialogOpen(true)}
            title="Passwort ändern"
          >
            <Key className="h-4 w-4" />
            <span className="hidden sm:inline">Passwort ändern</span>
          </Button>
          <Button variant="ghost" size="sm" onClick={logout}>
            <LogOut className="h-4 w-4" />
            Abmelden
          </Button>
        </div>
      )}

      <Dialog open={passwordDialogOpen} onOpenChange={(open) => {
        if (!open) resetDialog();
        else setPasswordDialogOpen(true);
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Passwort ändern</DialogTitle>
            <DialogDescription>
              Geben Sie Ihr aktuelles Passwort und das neue Passwort ein.
            </DialogDescription>
          </DialogHeader>

          {error && (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          {success && (
            <div className="rounded-md bg-emerald-500/10 p-3 text-sm text-emerald-600">
              Passwort erfolgreich geändert. Sie werden abgemeldet.
            </div>
          )}

          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="old-password">Aktuelles Passwort</Label>
              <Input
                id="old-password"
                type="password"
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
                autoComplete="current-password"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="new-password">Neues Passwort</Label>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="confirm-password">Neues Passwort bestätigen</Label>
              <Input
                id="confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={resetDialog}>
              Abbrechen
            </Button>
            <Button onClick={handlePasswordChange} disabled={loading}>
              {loading ? "Speichern..." : "Passwort ändern"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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
