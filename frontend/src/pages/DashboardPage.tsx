/**
 * Dashboard / Cockpit (UI-Kit) -- "Was ist heute zu tun?" auf einen Blick:
 * Status-Kacheln, Handlungsbedarf (dringende Fristen) und Schnellzugriff.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Building2,
  Users,
  CircleAlert,
  CalendarClock,
  Plus,
  ReceiptText,
  Calculator,
  BellRing,
  ShieldCheck,
  ChevronRight,
  CheckCircle2,
  type LucideIcon,
} from "lucide-react";
import {
  getMyOrganization,
  listFristen,
  listProperties,
  listPersons,
} from "../api/endpoints";
import type { Organization, DeadlineItem, DeadlineStatus } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { Card, CardHeader, CardTitle, CardContent } from "../components/ui/card";
import { Badge, type BadgeTone } from "../components/ui/badge";
import { StatCard } from "../components/ui/stat-card";

const KIND_LABELS: Record<string, string> = {
  BK_ABRECHNUNG: "Betriebskostenabrechnung",
  WEG_JAHRESABRECHNUNG: "WEG-Jahresabrechnung",
  WEG_VERMOEGENSBERICHT: "Vermögensbericht",
  WIRTSCHAFTSPLAN: "Wirtschaftsplan",
  OFFENE_POSTEN: "Überfällige offene Posten",
  BELEG_AUFBEWAHRUNG: "Beleg-Aufbewahrung",
};

const STATUS_META: Record<DeadlineStatus, { label: string; tone: BadgeTone }> = {
  OVERDUE: { label: "Überfällig", tone: "destructive" },
  DUE: { label: "Fällig", tone: "warning" },
  UPCOMING: { label: "Anstehend", tone: "primary" },
  INFO: { label: "Info", tone: "info" },
  OK: { label: "Erledigt", tone: "success" },
};

interface QuickAction {
  to: string;
  label: string;
  icon: LucideIcon;
}

const QUICK_ACTIONS: QuickAction[] = [
  { to: "/objekte", label: "Objekt anlegen", icon: Plus },
  { to: "/kosten", label: "Kosten erfassen", icon: ReceiptText },
  { to: "/abrechnungen", label: "Abrechnung erstellen", icon: Calculator },
  { to: "/fristen", label: "Fristen prüfen", icon: CalendarClock },
  { to: "/mahnungen", label: "Mahnungen", icon: BellRing },
  { to: "/personen", label: "Personen & Datenschutz", icon: ShieldCheck },
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 11) return "Guten Morgen";
  if (h < 18) return "Guten Tag";
  return "Guten Abend";
}

export function DashboardPage() {
  const { session } = useAuth();
  const [org, setOrg] = useState<Organization | null>(null);
  const [fristen, setFristen] = useState<DeadlineItem[]>([]);
  const [propertyCount, setPropertyCount] = useState<number | null>(null);
  const [personCount, setPersonCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getMyOrganization(),
      listFristen(todayIso()),
      listProperties(),
      listPersons(),
    ])
      .then(([o, f, props, persons]) => {
        if (cancelled) return;
        setOrg(o);
        setFristen(f);
        setPropertyCount(props.length);
        setPersonCount(persons.length);
      })
      .catch((err) => !cancelled && setError(String(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const overdue = fristen.filter((f) => f.status === "OVERDUE");
  const due = fristen.filter((f) => f.status === "DUE");
  const actionItems = [...overdue, ...due];

  const openItemsEntry = fristen.find((f) => f.kind === "OFFENE_POSTEN");
  const openItemsCount = openItemsEntry
    ? Number(openItemsEntry.detail.match(/\d+/)?.[0] ?? 0)
    : 0;

  const dateLabel = new Date().toLocaleDateString("de-DE", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <p className="text-sm text-muted-foreground">{dateLabel}</p>
        <h1 className="mt-0.5 text-2xl font-semibold tracking-tight">
          {greeting()}
          {org ? `, ${org.name}` : ""}
        </h1>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Stat tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Fristen überfällig"
          value={loading ? "…" : overdue.length}
          icon={AlertTriangle}
          tone={overdue.length > 0 ? "destructive" : "success"}
          hint={due.length > 0 ? `${due.length} demnächst fällig` : "alles im grünen Bereich"}
          to="/fristen"
        />
        <StatCard
          label="Objekte"
          value={loading ? "…" : (propertyCount ?? 0)}
          icon={Building2}
          tone="primary"
          to="/objekte"
        />
        <StatCard
          label="Personen"
          value={loading ? "…" : (personCount ?? 0)}
          icon={Users}
          tone="default"
          to="/personen"
        />
        <StatCard
          label="Überfällige Posten"
          value={loading ? "…" : openItemsCount}
          icon={CircleAlert}
          tone={openItemsCount > 0 ? "warning" : "success"}
          hint={openItemsCount > 0 ? "Mahnwesen prüfen" : "keine offenen Posten"}
          to="/offene-posten"
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Handlungsbedarf */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Handlungsbedarf</CardTitle>
            <Link
              to="/fristen"
              className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
            >
              Alle Fristen <ChevronRight className="h-4 w-4" />
            </Link>
          </CardHeader>
          <CardContent>
            {loading ? (
              <p className="text-sm text-muted-foreground">Lade…</p>
            ) : actionItems.length === 0 ? (
              <div className="flex items-center gap-3 rounded-md bg-emerald-50 px-4 py-6 text-emerald-700">
                <CheckCircle2 className="h-5 w-5 shrink-0" />
                <span className="text-sm font-medium">
                  Keine überfälligen oder fälligen Fristen. Alles erledigt.
                </span>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {actionItems.slice(0, 6).map((it, i) => (
                  <li key={i} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                    <Badge tone={STATUS_META[it.status].tone} className="shrink-0">
                      {STATUS_META[it.status].label}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {KIND_LABELS[it.kind] ?? it.kind}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {it.reference}
                      </p>
                    </div>
                    {it.due_date && (
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                        bis {formatDate(it.due_date)}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Schnellzugriff */}
        <Card>
          <CardHeader>
            <CardTitle>Schnellzugriff</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-2">
              {QUICK_ACTIONS.map((a) => (
                <Link
                  key={a.to}
                  to={a.to}
                  className="group flex items-center gap-3 rounded-md border border-border px-3 py-2.5 text-sm font-medium transition-colors hover:border-primary/40 hover:bg-accent"
                >
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-secondary text-foreground group-hover:bg-primary group-hover:text-primary-foreground">
                    <a.icon className="h-4 w-4" />
                  </span>
                  <span className="flex-1">{a.label}</span>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </Link>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {session?.role === "READ_ONLY" && (
        <p className="text-xs text-muted-foreground">
          Hinweis: Sie sind im Nur-Lesen-Modus angemeldet.
        </p>
      )}
    </div>
  );
}

function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}
