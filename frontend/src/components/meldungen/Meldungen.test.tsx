import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import * as endpoints from "../../api/endpoints";
import type { Meldung } from "../../api/types";
import { MeldungenPage } from "../../pages/MeldungenPage";
import { MeldungenLink } from "./MeldungenLink";
import { MELDUNGEN_POLL_MS, MeldungenProvider } from "./MeldungenProvider";

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
    target: "/teilungserklaerung/import/import-1", ...overrides,
  };
}

const waiting = () => meldung({ id: "import-2", state: "WAITING", state_label: "Wartet auf Verarbeitung", subject: "Neues Objekt: Teilung.pdf", target: "/teilungserklaerung/import/import-2" });
const review = () => meldung({ id: "import-3", state: "REVIEW", state_label: "Wartet auf Ihre Prüfung", target: "/teilungserklaerung/import/import-3/review" });
const failed = () => meldung({ id: "import-4", state: "FAILED", state_label: "Verarbeitung fehlgeschlagen", at: null });

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
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const list = vi.spyOn(endpoints, "listMeldungen").mockResolvedValue({ items });
    await act(async () => { frame(); });
    return list;
  }
  const tick = (times: number) => act(async () => { vi.advanceTimersByTime(MELDUNGEN_POLL_MS * times); });

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
});
