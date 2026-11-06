/**
 * Grouped navigation shared by Klar, Warm and the accessible mobile menu.
 * Admin-only links appear only for OWNER_ADMIN.
 */

import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
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
  House,
  Settings,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "../auth/useAuth";
import { useDesignTheme } from "../design/useDesignTheme";

interface Props {
  onClose: () => void;
  closeControl?: ReactNode;
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
      { to: "/", label: "Übersicht", icon: LayoutDashboard, end: true },
      { to: "/aufgaben", label: "Aufgaben", icon: ClipboardList },
      { to: "/briefe", label: "Briefe", icon: FileText },
      { to: "/fristen", label: "Fristenmonitor", icon: CalendarClock },
      { to: "/berichte", label: "Berichte", icon: FileSpreadsheet },
      { to: "/mandate", label: "Mandate", icon: Briefcase },
    ],
  },
  {
    label: "Stammdaten",
    items: [
      { to: "/objekte", label: "Objekte", icon: Building2 },
      { to: "/personen", label: "Personen", icon: Users },
    ],
  },
  {
    label: "Kosten & Belege",
    items: [
      { to: "/kosten", label: "Kostenpositionen", icon: ReceiptText },
      { to: "/belege", label: "Belege", icon: FileText },
      { to: "/posteingang", label: "Posteingang", icon: FileText },
      { to: "/klaerungsfaelle", label: "Klärungsfälle", icon: FileText },
    ],
  },
  {
    label: "Abrechnungen",
    items: [{ to: "/abrechnungen", label: "Betriebskosten", icon: Calculator }],
  },
  {
    label: "WEG",
    items: [
      { to: "/sonderumlagen", label: "Sonderumlagen", icon: Landmark },
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
      { to: "/kautionen", label: "Kautionen", icon: Wallet },
      { to: "/offene-posten", label: "Offene Posten", icon: CircleAlert },
      { to: "/mahnungen", label: "Mahnungen", icon: BellRing },
    ],
  },
  {
    label: "Verwaltung",
    adminOnly: true,
    items: [{ to: "/users", label: "Benutzer", icon: UserCog }],
  },
  {
    label: "Persönlich",
    items: [{ to: "/einstellungen", label: "Einstellungen", icon: Settings }],
  },
  {
    label: "Erweitert",
    adminOnly: true,
    items: [
      { to: "/altquellen", label: "Altquellen", icon: FileText },
      { to: "/berechtigungen", label: "Berechtigungen", icon: KeyRound },
      { to: "/seitenauszuege", label: "Seitenauszüge", icon: FileText },
    ],
  },
];

export function Sidebar({ onClose, closeControl }: Props) {
  const { session } = useAuth();
  const isAdmin = session?.role === "OWNER_ADMIN";
  return (
    <aside className="app-sidebar">
      <div className="app-brand">
        <span><House aria-hidden="true" /></span>verwalino
        {closeControl}
      </div>
      <NavigationLinks isAdmin={isAdmin} mobile={!!closeControl} onClose={onClose} />
    </aside>
  );
}

const PRIMARY_PATHS = ["/", "/objekte", "/posteingang"];

function NavigationLinks({ isAdmin, mobile, onClose }: { isAdmin: boolean; mobile: boolean; onClose: () => void }) {
  const { pathname } = useLocation();
  const { theme } = useDesignTheme();
  const groups = GROUPS.filter(group => !group.adminOnly || isAdmin);
  const primary = groups.flatMap(group => group.items).filter(item => PRIMARY_PATHS.includes(item.to));
  const secondaryGroups = groups.map(group => ({
    ...group,
    items: group.items.filter(item => !PRIMARY_PATHS.includes(item.to)),
  })).filter(group => group.items.length > 0);
  const activeGroup = secondaryGroups.find(group => group.items.some(item => isCurrentPath(pathname, item.to)));
  const link = (item: NavItem) => (
    <NavLink key={item.to} to={item.to} end={item.end} onClick={onClose} className="app-nav-link">
      <item.icon aria-hidden="true" />
      <span>{item.label}</span>
    </NavLink>
  );

  return (
    <nav className="app-navigation" aria-label="Hauptnavigation">
      <div className="primary-navigation">{primary.map(link)}</div>
      <details className="navigation-more" key={pathname} open={(mobile || theme === "klar") && !!activeGroup}>
        <summary>Weitere Bereiche<ChevronDown aria-hidden="true" /></summary>
        <div className="navigation-groups">
          {secondaryGroups.map(group => (
            <details className="navigation-group" key={group.label} open={group === activeGroup}>
              <summary>{group.label}<ChevronDown aria-hidden="true" /></summary>
              <div>{group.items.map(link)}</div>
            </details>
          ))}
        </div>
      </details>
    </nav>
  );
}

function isCurrentPath(pathname: string, path: string) {
  return pathname === path || pathname.startsWith(path + "/");
}
