/**
 * Dashboard / Cockpit (UI-Kit) -- "Was ist heute zu tun?" auf einen Blick:
 * Status-Kacheln, Handlungsbedarf (dringende Fristen) und Schnellzugriff.
 */

import { useEffect, useRef, useState } from "react";
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
  getFristenContext,
  listTasks,
  updateTask,
  listFristen,
  listProperties,
  listPersons,
} from "../api/endpoints";
import type { Organization, DeadlineItem, DeadlineStatus, Task } from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { DashboardKpis } from "../components/DashboardKpis";
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
  return calendarIso(new Date());
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 11) return "Guten Morgen";
  if (h < 18) return "Guten Tag";
  return "Guten Abend";
}

export function DashboardPage() {
  const { session } = useAuth();
  return <DashboardContent key={`${session?.organization_id}:${session?.user_id}:${session?.role}`} />;
}

function DashboardContent() {
  const { session } = useAuth();
  const [org, setOrg] = useState<Organization | null>(null);
  const [fristen, setFristen] = useState<DeadlineItem[] | null>(null);
  const [propertyCount, setPropertyCount] = useState<number | null>(null);
  const [properties, setProperties] = useState<Awaited<ReturnType<typeof listProperties>> | null>(null);
  const [personCount, setPersonCount] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [masterError, setMasterError] = useState<string | null>(null);
  const [masterLoading, setMasterLoading] = useState(true);
  const [scopeRequired, setScopeRequired] = useState<boolean | null>(null);
  const [revision, setRevision] = useState(0);
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [resultScope, setResultScope] = useState<string | null | undefined>(undefined);
  const loading = resultScope !== scopeId;

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getMyOrganization(), listProperties(), listPersons()])
      .then(([o, props, persons]) => {
        if (cancelled) return;
        setOrg(o.status === "fulfilled" ? o.value : null);
        setPropertyCount(props.status === "fulfilled" ? props.value.length : null);
        setProperties(props.status === "fulfilled" ? props.value : null);
        setPersonCount(persons.status === "fulfilled" ? persons.value.length : null);
        const failures = [o, props, persons].filter(result => result.status === "rejected");
        setMasterError(failures.length ? failures.map(result => String(result.reason)).join(" · ") : null);
        setMasterLoading(false);
      });
    return () => { cancelled = true; };
  }, [session?.organization_id, session?.user_id]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const context = await getFristenContext();
        if (cancelled) return;
        if (typeof context.requires_accounting_scope !== "boolean") throw new Error("Fristenkontext nicht verfügbar");
        setScopeRequired(context.requires_accounting_scope);
        if (context.requires_accounting_scope && !scopeId) {
          setFristen(null); setError(null); setResultScope(scopeId);
          return;
        }
        const rows = await listFristen(todayIso(), scopeId || undefined);
        if (!cancelled) { setFristen(rows); setError(null); setResultScope(scopeId); }
      } catch (reason) {
        if (!cancelled) { setFristen(null); setError(String(reason)); setResultScope(scopeId); }
      }
    })();
    return () => { cancelled = true; };
  }, [scopeId, revision, session?.organization_id, session?.user_id]);

  const financeAvailable = !loading && fristen !== null;
  const overdue = (fristen ?? []).filter((f) => f.status === "OVERDUE");
  const due = (fristen ?? []).filter((f) => f.status === "DUE");
  const actionItems = [...overdue, ...due];

  const openItemsEntry = fristen?.find((f) => f.kind === "OFFENE_POSTEN");
  const openItemsMatch = openItemsEntry?.detail.match(/\d+/);
  const openItemsCount = openItemsMatch ? Number(openItemsMatch[0]) : null;
  const openItemsAvailable = financeAvailable && openItemsCount !== null;

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

      <div className="form-grid max-w-xl">
        <AccountingScopeSelector capability="read" value={scopeId} onChange={id => { if (id !== scopeId) { setResultScope(undefined); setScopeId(id); } }} />
      </div>
      {scopeRequired && !scopeId && <p>Finanzielle Fristen: Abrechnungskreis auswählen</p>}
      {error && !loading && (
        <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error} <button onClick={() => { setResultScope(undefined); setRevision(n => n + 1); }}>Fristen erneut laden</button>
        </div>
      )}

      {masterError && <div role="alert">{masterError}</div>}

      {/* Stat tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Fristen überfällig"
          value={loading ? "…" : financeAvailable ? overdue.length : "—"}
          icon={AlertTriangle}
          tone={!financeAvailable ? "default" : overdue.length > 0 ? "destructive" : "success"}
          hint={loading ? undefined : !financeAvailable ? "Nicht verfügbar" : due.length > 0 ? `${due.length} demnächst fällig` : "alles im grünen Bereich"}
          to="/fristen"
        />
        <StatCard
          label="Objekte"
          value={masterLoading ? "…" : (propertyCount ?? "—")}
          icon={Building2}
          tone="primary"
          to="/objekte"
        />
        <StatCard
          label="Personen"
          value={masterLoading ? "…" : (personCount ?? "—")}
          icon={Users}
          tone="default"
          to="/personen"
        />
        <StatCard
          label="Überfällige Posten"
          value={loading ? "…" : openItemsCount ?? "—"}
          icon={CircleAlert}
          tone={!openItemsAvailable ? "default" : openItemsCount > 0 ? "warning" : "success"}
          hint={loading ? undefined : !financeAvailable ? "Nicht verfügbar" : !openItemsAvailable ? "Für diesen Bereich nicht ausgewiesen" : openItemsCount > 0 ? "Mahnwesen prüfen" : "keine offenen Posten"}
          to="/offene-posten"
        />
      </div>

      <DashboardKpis properties={properties} requiresScope={scopeRequired} sessionKey={`${session?.organization_id}:${session?.user_id}:${session?.role}`} />

      <DueTasks key={`${session?.organization_id}:${session?.user_id}:${session?.role}`} canWrite={session?.role === "OWNER_ADMIN" || session?.role === "CLERK"} />

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
            ) : !financeAvailable ? (
              <p className="text-sm text-muted-foreground">Fristenübersicht nicht verfügbar. Der Handlungsbedarf kann derzeit nicht ermittelt werden.</p>
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

function calendarIso(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function DueTasks({ canWrite }: { canWrite: boolean }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const locked = useRef(false);
  const alive = useRef(false);
  const generation = useRef(0);
  const end = new Date(); end.setDate(end.getDate() + 7);
  const dueUntil = calendarIso(end);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const request = ++generation.current;
    let current = true;
    void Promise.resolve().then(async () => {
      if (!current) return;
      setLoading(true);
      try {
        const page = await listTasks({ completed: false, due_until: dueUntil, offset, limit: 50 });
        if (current && request === generation.current) {
          setTasks(page.items.filter(task => !task.completed && !!task.due_date && task.due_date <= dueUntil));
          setNextOffset(page.next_offset); setError(null);
        }
      } catch (reason) { if (current && request === generation.current) setError(String(reason)); }
      finally { if (current && request === generation.current) setLoading(false); }
    });
    return () => { current = false; };
  }, [dueUntil, offset, revision]);
  async function complete(task: Task) {
    if (!canWrite || locked.current) return;
    locked.current = true; setPending(true); setError(null);
    const request = ++generation.current;
    try {
      await updateTask(task.id, { completed: true });
      if (alive.current && request === generation.current) setTasks(rows => rows?.filter(row => row.id !== task.id) ?? null);
    } catch (reason) { if (alive.current && request === generation.current) setError(String(reason)); }
    finally { locked.current = false; if (alive.current) { setPending(false); setLoading(false); } }
  }
  return <Card role="region" aria-label="Fällige Aufgaben"><CardHeader className="flex-row items-center justify-between"><CardTitle>Fällige Aufgaben</CardTitle><Link to="/aufgaben">Alle Aufgaben</Link></CardHeader><CardContent>
    <p className="text-sm text-muted-foreground">Offene Aufgaben bis einschließlich {formatDate(dueUntil)} · alle zugänglichen Zuständigkeiten</p>
    {loading && <p role="status">Aufgaben werden geladen…</p>}
    {error && <p role="alert">Aufgaben: {error}</p>}
    <button disabled={pending || loading} onClick={() => setRevision(n => n + 1)}>Aufgaben erneut laden</button>
    {tasks && <ul>{tasks.map(task => <li key={task.id} className="flex items-center gap-3 py-3"><div className="flex-1"><p>{task.title}</p><p className="text-sm">Fällig am {task.due_date ? formatDate(task.due_date) : "—"}{task.property_name ? ` · ${task.property_name}` : ""}</p></div>{canWrite && <button disabled={pending || loading} onClick={() => void complete(task)}>Erledigen</button>}</li>)}</ul>}
    {tasks?.length === 0 && !loading && !error && <p>Keine fälligen Aufgaben in diesem Zeitraum.</p>}
    <div className="flex gap-3"><button disabled={pending || loading || offset === 0} onClick={() => { setTasks(null); setOffset(Math.max(0, offset - 50)); }}>Vorherige Aufgaben</button><button disabled={pending || loading || nextOffset === null} onClick={() => { if (nextOffset !== null) { setTasks(null); setOffset(nextOffset); } }}>Weitere Aufgaben</button></div>
  </CardContent></Card>;
}
