import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import * as endpoints from "../../api/endpoints";
import type { Meldung } from "../../api/types";
import { MeldungenPage } from "../../pages/MeldungenPage";
import { MeldungenLink } from "./MeldungenLink";
import { MELDUNGEN_POLL_END_MS, MELDUNGEN_POLL_MS, MeldungenProvider } from "./MeldungenProvider";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  Reflect.deleteProperty(document, "visibilityState");
});

function meldung(overrides: Partial<Meldung> = {}): Meldung {
  return {
    kind: "DECLARATION_IMPORT", id: "import-1", title: "Teilungserklärung einlesen", subject: "Lindenallee 12",
    state: "RUNNING", state_label: "Läuft: Angaben auslesen", at: "2026-11-23T13:05:00Z",
    target: "/teilungserklaerung/import/import-1", stage: null, ...overrides,
  };
}

const waiting = () => meldung({ id: "import-2", state: "WAITING", state_label: "Wartet auf Verarbeitung", subject: "Neues Objekt: Teilung.pdf", target: "/teilungserklaerung/import/import-2" });
const review = () => meldung({ id: "import-3", state: "REVIEW", state_label: "Wartet auf Ihre Prüfung", target: "/teilungserklaerung/import/import-3/review" });
const failed = () => meldung({ id: "import-4", state: "FAILED", state_label: "Verarbeitung fehlgeschlagen", at: null });
const batch = (overrides: Partial<Meldung> = {}) => meldung({
  kind: "INTAKE_BATCH", id: "batch-1", title: "Dokumentenstapel", subject: "3 Dateien", state: "RUNNING", state_label: "Läuft", stage: "extraction",
  at: "2026-11-24T08:12:00Z", target: "/posteingang/batch-1", ...overrides,
});

/** The entry and the list read the same answer, as in the application frame. */
function frame() {
  return render(
    <MemoryRouter initialEntries={["/objekte"]}>
      <MeldungenProvider>
        <MeldungenLink />
        <Link to="/aufgaben">Aufgaben</Link>
        <MeldungenPage />
      </MeldungenProvider>
    </MemoryRouter>,
  );
}

const entry = () => screen.getByRole("link", { name: /^Meldungen/, description: "" });

describe("the entry of the application frame", () => {
  it("shows no number until the server has answered, then the number of Meldungen, and leads to the list", async () => {
    let answer!: (items: Meldung[]) => void;
    vi.spyOn(endpoints, "listMeldungen").mockReturnValue(new Promise((resolve) => { answer = (items) => resolve({ items }); }));
    frame();

    expect(screen.getByRole("link", { name: "Meldungen" })).toHaveAttribute("href", "/meldungen");
    expect(screen.getByRole("link", { name: "Meldungen" })).not.toHaveTextContent(/\d/);

    await act(async () => answer([meldung(), waiting()]));

    expect(screen.getByRole("link", { name: "Meldungen, 2 offen" })).toHaveTextContent("Meldungen2");
  });

  it("says 0 when there is nothing to look at, and the list says only that there are no Meldungen", async () => {
    vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [] });
    frame();

    expect(await screen.findByRole("link", { name: "Meldungen, 0 offen" })).toHaveTextContent("Meldungen0");
    expect(screen.getByText("Keine Meldungen vorhanden.")).toBeVisible();
    expect(screen.queryByRole("list", { name: "Meldungen" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("counts exactly the rows of the list", async () => {
    vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [meldung(), waiting(), review(), failed()] });
    frame();

    const rows = within(await screen.findByRole("list", { name: "Meldungen" })).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(entry()).toHaveAccessibleName(`Meldungen, ${rows.length} offen`);
  });

  it("asks the server again on every navigation", async () => {
    const list = vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [] });
    frame();
    await screen.findByRole("link", { name: "Meldungen, 0 offen" });
    expect(list).toHaveBeenCalledTimes(1);

    list.mockResolvedValue({ items: [meldung()] });
    fireEvent.click(screen.getByRole("link", { name: "Aufgaben" }));

    expect(await screen.findByRole("link", { name: "Meldungen, 1 offen" })).toBeVisible();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("the list of Meldungen", () => {
  it("is one row for each Meldung, with what it is about, its state, its start and the page it leads to", async () => {
    vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [meldung(), review(), failed()] });
    frame();

    const rows = within(await screen.findByRole("list", { name: "Meldungen" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Teilungserklärung einlesenLäuft: Angaben auslesenLindenallee 12Gestartet: 23.11.2026, 14:05 Uhr",
      "Teilungserklärung einlesenWartet auf Ihre PrüfungLindenallee 12Gestartet: 23.11.2026, 14:05 Uhr",
      "Teilungserklärung einlesenVerarbeitung fehlgeschlagenLindenallee 12",
    ]);
    expect(rows.map((row) => within(row).getByRole("link").getAttribute("href"))).toEqual([
      "/teilungserklaerung/import/import-1", "/teilungserklaerung/import/import-3/review", "/teilungserklaerung/import/import-1",
    ]);
  });

  it("shows a Posteingang batch with its files, its stage in the words of the batch page and its upload, and leads to the batch page", async () => {
    vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [
      batch(), batch({ id: "batch-2", subject: "1 Datei", state: "WAITING", state_label: "Wartet auf Verarbeitung", stage: "inspection", target: "/posteingang/batch-2" }),
      batch({ id: "batch-3", state: "FAILED", state_label: "Verarbeitung fehlgeschlagen", stage: "preparation", target: "/posteingang/batch-3" }), meldung(),
    ] });
    frame();

    const rows = within(await screen.findByRole("list", { name: "Meldungen" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "DokumentenstapelLäuft3 DateienStufe: AuswertungEingang vom 24.11.2026, 09:12 Uhr",
      "DokumentenstapelWartet auf Verarbeitung1 DateiStufe: DateiprüfungEingang vom 24.11.2026, 09:12 Uhr",
      "DokumentenstapelVerarbeitung fehlgeschlagen3 DateienStufe: DokumentvorbereitungEingang vom 24.11.2026, 09:12 Uhr",
      "Teilungserklärung einlesenLäuft: Angaben auslesenLindenallee 12Gestartet: 23.11.2026, 14:05 Uhr",
    ]);
    expect(rows.map((row) => within(row).getByRole("link").getAttribute("href"))).toEqual([
      "/posteingang/batch-1", "/posteingang/batch-2", "/posteingang/batch-3", "/teilungserklaerung/import/import-1",
    ]);
    expect(entry()).toHaveAccessibleName("Meldungen, 4 offen");
  });

  it("says nothing about a stage it has no word for, instead of showing the key", async () => {
    vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [batch({ stage: "toString" }), batch({ id: "batch-2", stage: "unbekannt" })] });
    frame();

    const rows = within(await screen.findByRole("list", { name: "Meldungen" })).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "DokumentenstapelLäuft3 DateienEingang vom 24.11.2026, 09:12 Uhr", "DokumentenstapelLäuft3 DateienEingang vom 24.11.2026, 09:12 Uhr",
    ]);
  });

  it("says that the request failed, with a way to ask again, and shows no number", async () => {
    const list = vi.spyOn(endpoints, "listMeldungen").mockRejectedValueOnce(new Error("offline"));
    frame();

    expect(await screen.findByRole("alert")).toHaveTextContent("Die Meldungen konnten nicht geladen werden.");
    expect(screen.getByRole("link", { name: "Meldungen" })).not.toHaveTextContent(/\d/);
    expect(screen.queryByText("Keine Meldungen vorhanden.")).toBeNull();

    list.mockResolvedValue({ items: [meldung()] });
    fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));

    expect(await screen.findByRole("link", { name: "Meldungen, 1 offen" })).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the last answer when a later request fails, and says so", async () => {
    const list = vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items: [meldung()] });
    frame();
    await screen.findByRole("link", { name: "Meldungen, 1 offen" });

    list.mockRejectedValue(new Error("offline"));
    fireEvent.click(screen.getByRole("link", { name: "Aufgaben" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Die Meldungen konnten nicht geladen werden.");
    expect(screen.getByRole("link", { name: "Meldungen, 1 offen" })).toBeVisible();
    expect(screen.getByRole("list", { name: "Meldungen" })).toBeVisible();
  });
});

describe("the refresh while the page stays where it is", () => {
  async function render_(items: Meldung[]) {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const list = vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items });
    await act(async () => { frame(); });
    return list;
  }
  const tick = (times: number) => act(async () => { vi.advanceTimersByTime(MELDUNGEN_POLL_MS * times); });
  /** One interval after the other, so that every answer arrives before the next interval is due, as in a browser. */
  const tickEach = async (times: number) => { for (let i = 0; i < times; i++) await tick(1); };
  const polls = MELDUNGEN_POLL_END_MS / MELDUNGEN_POLL_MS;

  it("asks again at a slow interval while a Meldung waits or runs", async () => {
    const list = await render_([meldung()]);
    expect(list).toHaveBeenCalledTimes(1);

    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);
    await tick(2);
    expect(list).toHaveBeenCalledTimes(4);
  });

  it("asks again at that interval for a Meldung that waits for processing as well", async () => {
    const list = await render_([waiting()]);
    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("asks again for a Posteingang batch that is processed, which ends with the answers like an import's poll", async () => {
    const list = await render_([batch()]);
    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);
    await tickEach(polls + 2);
    const calls = list.mock.calls.length;
    await tick(3);
    expect(list).toHaveBeenCalledTimes(calls);
  });

  it("does not ask between two navigations when no Meldung is waiting or running", async () => {
    const list = await render_([review(), failed()]);
    await tick(5);
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("does not ask while the tab is hidden, and asks when it is shown again", async () => {
    const list = await render_([meldung()]);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    await tick(3);
    expect(list).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("stops asking after a request failed, however the answer before it read, and asks again on the next navigation", async () => {
    // The session ended or the server failed while an import was running: no request every 20 seconds for as long as the tab is visible.
    const list = await render_([meldung()]);
    list.mockRejectedValue(new Error("401"));

    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent("Die Meldungen konnten nicht geladen werden.");
    await tick(5);
    expect(list).toHaveBeenCalledTimes(2);

    list.mockResolvedValue({ items: [meldung()] });
    await act(async () => { fireEvent.click(screen.getByRole("link", { name: "Aufgaben" })); });
    expect(list).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).toBeNull();
    await tick(1);
    expect(list).toHaveBeenCalledTimes(4);
  });

  it("asks again after a request failed and the tab is shown again, and the poll goes on once that request is answered", async () => {
    const list = await render_([meldung()]);
    list.mockRejectedValueOnce(new Error("offline"));
    await tick(1);
    expect(list).toHaveBeenCalledTimes(2);

    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); });
    expect(list).toHaveBeenCalledTimes(3);
    await tick(1);
    expect(list).toHaveBeenCalledTimes(4);
  });

  it("ends the poll when the answer has not changed for the documented time, and a navigation starts it again", async () => {
    // An import that stays queued (no worker runs) is the same answer every time.
    const list = await render_([waiting()]);

    await tickEach(Math.floor(polls / 2));
    expect(list.mock.calls.length).toBeGreaterThan(polls / 2);
    await tickEach(Math.ceil(polls / 2));
    const atEnd = list.mock.calls.length;
    await tickEach(5);
    expect(list).toHaveBeenCalledTimes(atEnd);

    await act(async () => { fireEvent.click(screen.getByRole("link", { name: "Aufgaben" })); });
    expect(list).toHaveBeenCalledTimes(atEnd + 1);
    await tick(2);
    expect(list).toHaveBeenCalledTimes(atEnd + 3);
  });

  it("does not end the poll while the answers keep changing", async () => {
    let step = 0;
    const list = vi.spyOn(endpoints, "listMeldungen").mockImplementation(
      async () => ({ items: [meldung({ state_label: `Läuft: Schritt ${++step}` })] }),
    );
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    await act(async () => { frame(); });

    await tickEach(polls * 2);
    expect(list).toHaveBeenCalledTimes(polls * 2 + 1);
  });
});
