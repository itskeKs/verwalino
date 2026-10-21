import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { createTask, deleteTask, listLeases, listProperties, listTaskAssignees, listTasks, listUnits, updateTask } from "../api/endpoints";
import type { LeaseRecord, Property, Task, TaskInput, TaskPage, Unit, User } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";

interface Choices { properties: Property[]; units: Unit[]; leases: LeaseRecord[]; users: User[] }
interface Form { title: string; note: string; due: string; assigned: string; property: string; unit: string; lease: string }
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function TasksPage() {
  const location = useLocation();
  const { session } = useAuth();
  return <TasksContent key={`${location.search}:${session?.organization_id}:${session?.user_id}:${session?.role}`} />;
}

function TasksContent() {
  const { session } = useAuth();
  const [params] = useSearchParams();
  const fixedProperty = params.get("property_id") || "";
  const fixedUnit = params.get("unit_id") || "";
  const fixedLease = params.get("lease_agreement_id") || "";
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  const [status, setStatus] = useState("open");
  const [propertyFilter, setPropertyFilter] = useState(fixedProperty);
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; page: TaskPage } | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [choices, setChoices] = useState<Choices | null>(null);
  const [choiceError, setChoiceError] = useState<string | null>(null);
  const [choiceRevision, setChoiceRevision] = useState(0);
  const [editing, setEditing] = useState<Task | "new" | null>(null);
  const [deleting, setDeleting] = useState<Task | null>(null);
  const [form, setForm] = useState<Form>({ title: "", note: "", due: "", assigned: "", property: "", unit: "", lease: "" });
  const [pending, setPending] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const lock = useRef(false);
  const alive = useRef(false);
  const generation = useRef(0);
  const autoOpened = useRef(false);
  const key = JSON.stringify([status, propertyFilter, fixedUnit, fixedLease, assigneeFilter, offset]);
  const page = result?.key === key ? result.page : null;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  useEffect(() => {
    let current = true;
    Promise.all([listProperties(), listUnits(), listLeases(), listTaskAssignees()])
      .then(([properties, units, leases, users]) => { if (current) { setChoices({ properties, units, leases, users }); setChoiceError(null); } })
      .catch(error => { if (current) setChoiceError(message(error)); });
    return () => { current = false; };
  }, [choiceRevision]);

  useEffect(() => {
    const request = ++generation.current;
    let current = true;
    void Promise.resolve().then(async () => {
      if (!current) return;
      setLoading(true); setListError(null);
      try {
        const data = await listTasks({
          ...(status === "all" ? {} : { completed: status === "completed" }),
          ...(propertyFilter ? { property_id: propertyFilter } : {}),
          ...(fixedUnit ? { unit_id: fixedUnit } : {}),
          ...(fixedLease ? { lease_agreement_id: fixedLease } : {}),
          ...(assigneeFilter ? { assigned_user_id: assigneeFilter } : {}), offset, limit: 50,
        });
        if (current && request === generation.current) setResult({ key, page: data });
      } catch (error) { if (current && request === generation.current) setListError(message(error)); }
      finally { if (current && request === generation.current) setLoading(false); }
    });
    return () => { current = false; };
  }, [key, status, propertyFilter, fixedUnit, fixedLease, assigneeFilter, offset, revision]);

  const contextProperty = choices?.properties.find(p => p.id === fixedProperty);
  const contextUnit = choices?.units.find(u => u.id === fixedUnit);
  const contextLease = choices?.leases.find(l => l.id === fixedLease);
  const contextValid = !!choices && (!fixedProperty || !!contextProperty)
    && (!fixedUnit || !!contextUnit && (!fixedProperty || contextUnit.property_id === fixedProperty))
    && (!fixedLease || !!contextLease && (!fixedUnit || contextLease.unit_id === fixedUnit)
      && (!fixedProperty || choices.units.some(u => u.id === contextLease.unit_id && u.property_id === fixedProperty)));
  const hasContext = !!(fixedProperty || fixedUnit || fixedLease);
  const initialUnit = fixedUnit || contextLease?.unit_id || "";
  const initialProperty = fixedProperty || choices?.units.find(u => u.id === initialUnit)?.property_id || "";

  useEffect(() => {
    if (!choices || !contextValid || !canWrite || params.get("create") !== "1" || autoOpened.current) return;
    autoOpened.current = true;
    setForm({ title: "", note: "", due: "", assigned: session?.user_id || "", property: initialProperty, unit: initialUnit, lease: fixedLease });
    setEditing("new");
  }, [choices, contextValid, canWrite, params, session?.user_id, initialProperty, initialUnit, fixedLease]);

  function openEditor(task?: Task) {
    if (lock.current || !choices || !contextValid || !canWrite) return;
    setMutationError(null);
    setForm(task ? { title: task.title, note: task.note, due: task.due_date || "", assigned: task.assigned_user_id, property: task.property_id || "", unit: task.unit_id || "", lease: task.lease_agreement_id || "" }
      : { title: "", note: "", due: "", assigned: session?.user_id || "", property: initialProperty || propertyFilter, unit: initialUnit, lease: fixedLease });
    setEditing(task || "new");
  }
  async function mutate(action: () => Promise<unknown>, after?: () => void) {
    if (lock.current || !canWrite) return;
    lock.current = true; generation.current++; setPending(true); setMutationError(null);
    try {
      await action();
      if (alive.current) { after?.(); setRevision(n => n + 1); }
    } catch (error) { if (alive.current) { setMutationError(message(error)); setLoading(false); } }
    finally { lock.current = false; if (alive.current) setPending(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!editing || lock.current || !choices || !contextValid) return;
    if (!form.title.trim() || form.title.trim().length > 255 || form.note.length > 10000) { setMutationError("Bitte Titel und Notiz prüfen."); return; }
    const old = editing === "new" ? null : editing;
    const knownAssignee = choices.users.some(u => u.id === form.assigned) || old?.assigned_user_id === form.assigned;
    if (!knownAssignee || form.property && !choices.properties.some(p => p.id === form.property)
      || form.unit && !choices.units.some(u => u.id === form.unit && u.property_id === form.property)
      || form.lease && !choices.leases.some(l => l.id === form.lease && l.unit_id === form.unit)) {
      setMutationError("Auswahl nicht verfügbar. Bitte Zuständigkeit und Kontext prüfen."); return;
    }
    const body: TaskInput = { title: form.title.trim(), note: form.note, due_date: form.due || null,
      assigned_user_id: form.assigned, property_id: form.property || null, unit_id: form.unit || null, lease_agreement_id: form.lease || null };
    void mutate(() => old ? updateTask(old.id, body) : createTask(body), () => setEditing(null));
  }
  const field = (name: keyof Form, value: string) => setForm(f => ({ ...f, [name]: value }));
  const editTask = editing && editing !== "new" ? editing : null;
  const contextText = [contextProperty?.name || choices?.properties.find(p => p.id === initialProperty)?.name, contextUnit ? `Einheit ${contextUnit.number}` : null, contextLease ? `Mietvertrag ab ${contextLease.valid_from}` : null].filter(Boolean).join(" · ");

  return <div className="space-y-4">
    <div className="flex items-center justify-between"><h1>Aufgaben</h1>{canWrite && <button className="btn btn-primary" disabled={pending || !contextValid} onClick={() => openEditor()}>Aufgabe anlegen</button>}</div>
    {hasContext && contextValid && <p>Fester Kontext: {contextText}. Bitte vor dem Speichern prüfen.</p>}
    {choices && !contextValid && <p role="alert" className="error-text">Kontext nicht verfügbar oder nicht zusammengehörig. Bitte den Aufgabenlink erneut aus Objekt oder Einheit öffnen.</p>}
    {choiceError && <div role="alert">Auswahl konnte nicht geladen werden: {choiceError} <button disabled={pending} onClick={() => setChoiceRevision(n => n + 1)}>Auswahl erneut laden</button></div>}
    <fieldset disabled={pending} className="form-grid">
      <label>Status<select aria-label="Status" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="open">Offen</option><option value="completed">Erledigt</option><option value="all">Alle</option></select></label>
      <label>Objektfilter<select aria-label="Objektfilter" value={propertyFilter} disabled={hasContext} onChange={e => { setPropertyFilter(e.target.value); setOffset(0); }}><option value="">Alle Objekte</option>{choices?.properties.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label>Zuständigkeitsfilter<select aria-label="Zuständigkeitsfilter" value={assigneeFilter} onChange={e => { setAssigneeFilter(e.target.value); setOffset(0); }}><option value="">Alle Zuständigen</option>{choices?.users.map(u => <option key={u.id} value={u.id}>{u.display_name || u.email}</option>)}</select></label>
    </fieldset>
    <button disabled={pending || loading} onClick={() => setRevision(n => n + 1)}>Erneut laden</button>
    {loading && <p role="status">Aufgaben werden geladen…</p>}
    {listError && <p role="alert">Aufgaben konnten nicht geladen werden: {listError}</p>}
    {mutationError && !editing && !deleting && <p role="alert">{mutationError}</p>}
    {page && <><ul className="space-y-3">{page.items.map(task => <li key={task.id} className="card">
      <h2>{task.title}</h2><p className="whitespace-pre-wrap">{task.note}</p>
      <p>{task.completed ? "Erledigt" : "Offen"} · {task.due_date ? `Fällig am ${task.due_date}` : "Ohne Fälligkeit"} · {task.assigned_user_name || "Zuständig zugeordnet"}</p>
      <p>{[task.property_name, task.unit_number ? `Einheit ${task.unit_number}` : null, task.lease_label].filter(Boolean).join(" · ") || "Ohne Objektbindung"}</p>
      {canWrite && <div className="flex gap-2"><button disabled={pending || !choices} onClick={() => openEditor(task)}>Bearbeiten</button><button disabled={pending} onClick={() => void mutate(() => updateTask(task.id, { completed: !task.completed }))}>{task.completed ? "Wiederöffnen" : "Erledigen"}</button><button disabled={pending} onClick={() => { setMutationError(null); setDeleting(task); }}>Löschen</button></div>}
    </li>)}</ul>{!page.items.length && !loading && <p>Keine Aufgaben gefunden.</p>}</>}
    <div className="flex gap-3"><button disabled={pending || loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Zurück</button><span>Seite {Math.floor(offset / 50) + 1}</span><button disabled={pending || loading || page?.next_offset == null} onClick={() => { if (page?.next_offset != null) setOffset(page.next_offset); }}>Weiter</button></div>
    <Dialog open={!!editing} onOpenChange={open => { if (!open && !lock.current) setEditing(null); }}>
      <DialogContent onEscapeKeyDown={e => { if (lock.current) e.preventDefault(); }} onPointerDownOutside={e => { if (lock.current) e.preventDefault(); }} className="max-h-[90vh] overflow-y-auto">
        <DialogTitle>{editing === "new" ? "Aufgabe anlegen" : "Aufgabe bearbeiten"}</DialogTitle>
        <DialogDescription>{hasContext ? `Fester Kontext: ${contextText}. Bitte vor dem Speichern prüfen.` : "Aufgaben können auch ohne Objektbindung angelegt werden."}</DialogDescription>
        <form onSubmit={submit}><fieldset disabled={pending} className="space-y-3">
          <label className="block">Titel *<input required maxLength={255} value={form.title} onChange={e => field("title", e.target.value)} /></label>
          <label className="block">Notiz<textarea maxLength={10000} value={form.note} onChange={e => field("note", e.target.value)} /></label>
          <label className="block">Fällig am<input type="date" value={form.due} onChange={e => field("due", e.target.value)} /></label>
          <label className="block">Zuständig<select aria-label="Zuständig" required value={form.assigned} onChange={e => field("assigned", e.target.value)}><option value="">Bitte wählen</option>{choices?.users.map(u => <option key={u.id} value={u.id}>{u.display_name || u.email}</option>)}{editTask && !choices?.users.some(u => u.id === editTask.assigned_user_id) && <option value={editTask.assigned_user_id}>{editTask.assigned_user_name || "Bisher zuständig"} (bisherige Zuordnung)</option>}</select></label>
          <label className="block">Objekt<select aria-label="Objekt" disabled={hasContext} value={form.property} onChange={e => setForm(f => ({ ...f, property: e.target.value, unit: "", lease: "" }))}><option value="">Ohne Objektbindung</option>{choices?.properties.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label className="block">Einheit<select aria-label="Einheit" disabled={!!(fixedUnit || fixedLease)} value={form.unit} onChange={e => setForm(f => ({ ...f, unit: e.target.value, lease: "" }))}><option value="">Keine Einheit</option>{choices?.units.filter(u => u.property_id === form.property).map(u => <option key={u.id} value={u.id}>Einheit {u.number}{u.location ? ` · ${u.location}` : ""}</option>)}</select></label>
          <label className="block">Mietvertrag<select aria-label="Mietvertrag" disabled={!!fixedLease} value={form.lease} onChange={e => field("lease", e.target.value)}><option value="">Kein Mietvertrag</option>{choices?.leases.filter(l => l.unit_id === form.unit).map(l => <option key={l.id} value={l.id}>Mietvertrag ab {l.valid_from}{l.valid_to ? ` bis ${l.valid_to}` : " (offen)"}</option>)}</select></label>
          {mutationError && <p role="alert">{mutationError}</p>}
          <div className="flex gap-3"><button className="btn btn-primary" type="submit">Speichern</button><button type="button" onClick={() => { if (!lock.current) setEditing(null); }}>Abbrechen</button></div>
        </fieldset></form>
      </DialogContent>
    </Dialog>
    <Dialog open={!!deleting} onOpenChange={open => { if (!open && !lock.current) setDeleting(null); }}><DialogContent onEscapeKeyDown={e => { if (lock.current) e.preventDefault(); }} onPointerDownOutside={e => { if (lock.current) e.preventDefault(); }}><DialogTitle>Aufgabe löschen?</DialogTitle><DialogDescription>Die Aufgabe „{deleting?.title}“ wird gelöscht.</DialogDescription>{mutationError && <p role="alert">{mutationError}</p>}<button disabled={pending} onClick={() => { if (deleting) void mutate(() => deleteTask(deleting.id), () => setDeleting(null)); }}>Aufgabe löschen</button><button disabled={pending} onClick={() => { if (!lock.current) setDeleting(null); }}>Abbrechen</button></DialogContent></Dialog>
  </div>;
}
