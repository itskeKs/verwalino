/**
 * Modern sidebar navigation (UI-Kit) -- dark rail with grouped, icon-led links.
 *
 * Desktop (md+): static, always visible. Mobile: off-canvas, slides in when
 * `open`. Admin-only links appear only for OWNER_ADMIN.
 */

import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  CalendarClock,
  Briefcase,
  Building2,
  Users,
  ReceiptText,
  FileText,
  Calculator,
  ClipboardList,
  FileSpreadsheet,
  Landmark,
  Vote,
  Gavel,
  Wallet,
  ArrowLeftRight,
  BookOpenText,
  CircleAlert,
  BellRing,
  UserCog,
  KeyRound,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/useAuth";
import { cn } from "../lib/utils";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
  adminOnly?: boolean;
}

const GROUPS: NavGroup[] = [
  {
    label: "Übersicht",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
      { to: "/fristen", label: "Fristenmonitor", icon: CalendarClock },
      { to: "/mandate", label: "Mandate", icon: Briefcase },
    ],
  },
  {
    label: "Stammdaten",
    items: [
      { to: "/objekte", label: "Objekte", icon: Building2 },
      { to: "/quellenfreigaben", label: "Quellenfreigaben", icon: FileText },
      { to: "/personen", label: "Personen", icon: Users },
    ],
  },
  {
    label: "Kosten & Belege",
    items: [
      { to: "/kosten", label: "Kostenpositionen", icon: ReceiptText },
      { to: "/belege", label: "Belege", icon: FileText },
    ],
  },
  {
    label: "Abrechnungen",
    items: [{ to: "/abrechnungen", label: "Betriebskosten", icon: Calculator }],
  },
  {
    label: "WEG",
    items: [
      { to: "/wirtschaftsplaene", label: "Wirtschaftspläne", icon: ClipboardList },
      { to: "/weg-abrechnungen", label: "WEG-Abrechnungen", icon: FileSpreadsheet },
      { to: "/vermoegensberichte", label: "Vermögensberichte", icon: Landmark },
    ],
  },
  {
    label: "Versammlungen",
    items: [
      { to: "/versammlungen", label: "Versammlungen", icon: Vote },
      { to: "/beschluss-sammlung", label: "Beschluss-Sammlung", icon: Gavel },
    ],
  },
  {
    label: "Zahlungen",
    items: [
      { to: "/bankkonten", label: "Bankkonten", icon: Wallet },
      { to: "/zahlungen", label: "Zahlungen", icon: ArrowLeftRight },
      { to: "/mietkonto", label: "Mietkonto", icon: BookOpenText },
      { to: "/offene-posten", label: "Offene Posten", icon: CircleAlert },
      { to: "/mahnungen", label: "Mahnungen", icon: BellRing },
    ],
  },
  {
    label: "Verwaltung",
    adminOnly: true,
    items: [{ to: "/users", label: "Benutzer", icon: UserCog }],
  },
];

export function Sidebar({ open, onClose }: Props) {
  const { session } = useAuth();
  const isAdmin = session?.role === "OWNER_ADMIN";

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex w-64 flex-col bg-sidebar text-sidebar-foreground transition-transform duration-200 md:static md:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full",
      )}
    >
      {/* Brand */}
      <div className="flex h-16 shrink-0 items-center gap-2.5 px-5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-sidebar-accent text-sidebar-accent-foreground">
          <KeyRound className="h-4 w-4" />
        </span>
        <span className="text-[0.95rem] font-semibold tracking-tight text-white">
          Hausverwaltung
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 pb-6">
        {GROUPS.filter((g) => !g.adminOnly || isAdmin).map((group) => (
          <div key={group.label} className="mb-1">
            <p className="px-3 pb-1 pt-4 text-[0.68rem] font-semibold uppercase tracking-wider text-sidebar-foreground/40">
              {group.label}
            </p>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onClose}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground shadow-sm"
                      : "text-sidebar-foreground/75 hover:bg-white/5 hover:text-white",
                  )
                }
              >
                <item.icon className="h-[1.05rem] w-[1.05rem] shrink-0" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
