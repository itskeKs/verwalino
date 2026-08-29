import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as knowledgeApi from "../api/endpoints";
import { ApiError } from "../api/client";
import type { PropertyKnowledgeOverview, KnowledgeAnswerOut, KnowledgeSearchResponse } from "../api/types";
import { PropertyKnowledgePage } from "./PropertyKnowledgePage";
import { searchPropertyKnowledge } from "../api/endpoints";

const source = {
  id: "source-1", document_id: "document-1", document_title: "Teilungserklärung", pdf_page: 7,
  printed_page: "5", section: "§ 8 Abs. 5", quote: "Die Instandsetzung obliegt dem Sondereigentümer.",
};

const overview: PropertyKnowledgeOverview = {
  property_id: "property-1",
  documents: [{ id: "document-1", doc_type: "DECLARATION", title: "Teilungserklärung", document_date: "2020-01-01", deed_reference: "UR 1/2020", page_count: 24, archived_at: null }],
  document_relations: [],
  open_rule_topics: ["REPAIRS"],
  rules_by_topic: {
    REPAIRS: [{
      id: "rule-1", kind: "RULE", topic: "REPAIRS", title: "Reparaturen", summary: "Zuständigkeiten für Reparaturen.", status: "EFFECTIVE",
      scope: { units: ["Wohnungen"] }, effect: { responsibility: "Sondereigentümer", exceptions: ["Gemeinschaftseigentum"] }, sources: [source], score: 1,
      relations: [],
    }],
  },
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/objekte/property-1/wissen"]}>
      <Routes><Route path="/objekte/:propertyId/wissen" element={<PropertyKnowledgePage />} /></Routes>
    </MemoryRouter>,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Dokumente & Wissen", () => {
  it("encodes a search query exactly once through URLSearchParams", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      property_id: "property-1", normalized_query: "§ 8 & Heizung", limit: 20, include_history: false, hits: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await searchPropertyKnowledge("property-1", "§ 8 & Heizung");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/properties/property-1/knowledge/search?q=%C2%A7+8+%26+Heizung",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("shows approved documents, effective rules and exact source metadata", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue({
      ...overview,
      document_relations: [{ id: "relation-1", source_document_id: "document-1", target_document_id: "document-2", relation_type: "AMENDS", status: "EFFECTIVE" }],
      documents: [...overview.documents, { id: "document-2", doc_type: "AMENDMENT", title: "Nachtrag", document_date: null, deed_reference: null, page_count: 2, archived_at: null }],
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Dokumente & Wissen" })).toBeVisible();
    expect(screen.getAllByText("Teilungserklärung").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Reparaturen" })).toBeVisible();
    expect(screen.getByText(/Sondereigentümer/)).toBeVisible();
    const sourceAction = screen.getByRole("button", { name: /quelle auf seite 7/i });
    expect(sourceAction).toHaveTextContent("Teilungserklärung");
    expect(sourceAction).toHaveTextContent("§ 8 Abs. 5");
    expect(sourceAction).toHaveTextContent("PDF-Seite 7");
    expect(sourceAction).toHaveTextContent("gedruckte Seite 5");
    fireEvent.click(sourceAction);
    expect(screen.getByTitle("Originalquelle: Teilungserklärung")).toHaveAttribute(
      "src", "/api/properties/property-1/documents/document-1/content#page=7",
    );
    const dialog = screen.getByRole("dialog", { name: "Originalquelle: Teilungserklärung" });
    expect(within(dialog).getByText(/§ 8 Abs. 5/)).toBeVisible();
    expect(within(dialog).getByText(/gedruckte Seite 5/)).toBeVisible();
    expect(screen.getByText("AMENDS: Nachtrag")).toBeVisible();
    expect(screen.getByRole("button", { name: "PDF neu laden" })).toBeVisible();
    expect(screen.getByRole("link", { name: "PDF separat öffnen" })).toHaveAttribute("href", "/api/properties/property-1/documents/document-1/content#page=7");
  });

  it("uses a dialog viewer that focuses, recovers PDF loading, and returns focus on escape", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(overview);
    renderPage();
    await screen.findByText("Reparaturen");
    const trigger = screen.getByRole("button", { name: /quelle auf seite 7/i });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Originalquelle: Teilungserklärung" });
    expect(screen.getByRole("button", { name: "Schließen" })).toHaveFocus();
    const firstFrame = screen.getByTitle("Originalquelle: Teilungserklärung");
    fireEvent(firstFrame, new Event("error", { bubbles: true }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/PDF konnte nicht geladen werden/i);
    fireEvent.click(screen.getByRole("button", { name: "PDF neu laden" }));
    const reloadedFrame = screen.getByTitle("Originalquelle: Teilungserklärung");
    expect(reloadedFrame).not.toBe(firstFrame);
    expect(reloadedFrame).toHaveAttribute("src", "/api/properties/property-1/documents/document-1/content#page=7");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps deterministic search usable when the Q&A provider is unavailable", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(overview);
    const search = vi.spyOn(knowledgeApi, "searchPropertyKnowledge").mockResolvedValue({
      property_id: "property-1", normalized_query: "Heizkosten", limit: 20, include_history: false,
      hits: [{ ...overview.rules_by_topic.REPAIRS[0], id: "rule-heating", topic: "HEATING", title: "Heizkosten", sources: [source] }],
    } satisfies KnowledgeSearchResponse);
    vi.spyOn(knowledgeApi, "queryPropertyKnowledge").mockRejectedValue(new ApiError(503, "QA_DISABLED"));
    renderPage();
    await screen.findByRole("heading", { name: "Dokumente & Wissen" });

    fireEvent.change(screen.getByLabelText("Gebäudewissen durchsuchen"), { target: { value: "Heizkosten" } });
    await waitFor(() => expect(search).toHaveBeenCalledWith("property-1", "Heizkosten", expect.objectContaining({ includeHistory: false })));
    expect(await screen.findByText("Heizkosten")).toBeVisible();

    fireEvent.change(screen.getByRole("textbox", { name: "Frage an Gebäudewissen" }), { target: { value: "Wie werden Heizkosten verteilt?" } });
    fireEvent.click(screen.getByRole("button", { name: "Frage stellen" }));
    expect(await screen.findByText(/vorübergehend nicht verfügbar/i)).toBeVisible();
    expect(screen.getByText("Heizkosten")).toBeVisible();
  });

  it("renders Q&A answers as text with cited source metadata", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(overview);
    vi.spyOn(knowledgeApi, "queryPropertyKnowledge").mockResolvedValue({
      status: "ANSWER", answer: "<strong>70 % nach Verbrauch</strong>", fact_ids: ["fact-1"], rule_ids: ["rule-1"], source_ids: ["source-1"], sources: [source],
    } satisfies KnowledgeAnswerOut);
    renderPage();
    await screen.findByRole("heading", { name: "Dokumente & Wissen" });
    fireEvent.change(screen.getByRole("textbox", { name: "Frage an Gebäudewissen" }), { target: { value: "Heizkosten?" } });
    fireEvent.click(screen.getByRole("button", { name: "Frage stellen" }));
    const renderedAnswer = await screen.findByText("<strong>70 % nach Verbrauch</strong>");
    expect(renderedAnswer).toBeVisible();
    expect(renderedAnswer.tagName).toBe("P");
    expect(screen.getByText(/Belegte Fakten: 1/)).toBeVisible();
  });

  it("hides superseded rules until history is requested and keeps clarification visible", async () => {
    const withHistory: PropertyKnowledgeOverview = {
      ...overview,
      rules_by_topic: {
        REPAIRS: [
          ...overview.rules_by_topic.REPAIRS,
          { ...overview.rules_by_topic.REPAIRS[0], id: "rule-old", title: "Frühere Reparaturregel", status: "SUPERSEDED" },
          { ...overview.rules_by_topic.REPAIRS[0], id: "rule-retracted", title: "Zurückgezogene Regel", status: "RETRACTED" },
          { ...overview.rules_by_topic.REPAIRS[0], id: "rule-clarify", title: "Unklare Zuständigkeit", status: "NEEDS_CLARIFICATION" },
        ],
      },
    };
    const getOverview = vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(withHistory);
    renderPage();

    expect(await screen.findByText("Unklare Zuständigkeit")).toBeVisible();
    expect(screen.getByText("⚠ Klärungsbedarf")).toBeVisible();
    expect(screen.queryByText("Frühere Reparaturregel")).toBeNull();
    expect(screen.queryByText("Zurückgezogene Regel")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Verlauf anzeigen" }));
    expect(await screen.findByText("Frühere Reparaturregel")).toBeVisible();
    expect(getOverview).toHaveBeenLastCalledWith("property-1", { includeHistory: true });
  });

  it("ignores late search results after a query change and after clearing", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(overview);
    const first = deferred<KnowledgeSearchResponse>();
    const second = deferred<KnowledgeSearchResponse>();
    const search = vi.spyOn(knowledgeApi, "searchPropertyKnowledge")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    renderPage();
    await screen.findByRole("heading", { name: "Dokumente & Wissen" });
    vi.useFakeTimers();

    const input = screen.getByLabelText("Gebäudewissen durchsuchen");
    fireEvent.change(input, { target: { value: "alt" } });
    await vi.advanceTimersByTimeAsync(300);
    const firstSignal = search.mock.calls[0]?.[2]?.signal;
    let abortedInsideChangeEvent = false;
    document.addEventListener("change", () => {
      abortedInsideChangeEvent = firstSignal?.aborted ?? false;
    }, { once: true });
    fireEvent.change(input, { target: { value: "neu" } });
    expect(abortedInsideChangeEvent).toBe(true);
    expect(firstSignal?.aborted).toBe(true);
    first.reject(new Error("stale search failure"));
    await Promise.resolve();
    expect(screen.queryByRole("alert")).toBeNull();

    await vi.advanceTimersByTimeAsync(300);
    const secondSignal = search.mock.calls[1]?.[2]?.signal;
    let abortedInsideClearEvent = false;
    document.addEventListener("change", () => {
      abortedInsideClearEvent = secondSignal?.aborted ?? false;
    }, { once: true });
    fireEvent.change(input, { target: { value: "" } });
    expect(abortedInsideClearEvent).toBe(true);
    second.resolve({ property_id: "property-1", normalized_query: "neu", limit: 20, include_history: false, hits: [{ ...overview.rules_by_topic.REPAIRS[0], id: "new", title: "Neues Ergebnis" }] });
    await Promise.resolve();
    expect(screen.queryByText("Neues Ergebnis")).toBeNull();
  });

  it("expands rules accessibly and distinguishes Q&A abstention states", async () => {
    vi.spyOn(knowledgeApi, "getPropertyKnowledge").mockResolvedValue(overview);
    vi.spyOn(knowledgeApi, "queryPropertyKnowledge")
      .mockResolvedValueOnce({ status: "NOT_DOCUMENTED", answer: "Nicht belegt.", fact_ids: [], rule_ids: [], source_ids: [], sources: [] })
      .mockResolvedValueOnce({ status: "NEEDS_CLARIFICATION", answer: "Widersprüchlich belegt.", fact_ids: [], rule_ids: [], source_ids: [], sources: [] });
    renderPage();
    await screen.findByText("Reparaturen");
    const expander = screen.getByRole("button", { name: "Reparaturen ein-/ausklappen" });
    expect(expander).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(expander);
    expect(expander).toHaveAttribute("aria-expanded", "false");

    const question = screen.getByRole("textbox", { name: "Frage an Gebäudewissen" });
    fireEvent.change(question, { target: { value: "Frage eins" } });
    fireEvent.click(screen.getByRole("button", { name: "Frage stellen" }));
    expect(await screen.findByRole("heading", { name: "In den freigegebenen Unterlagen nicht dokumentiert" })).toBeVisible();
    fireEvent.change(question, { target: { value: "Frage zwei" } });
    fireEvent.click(screen.getByRole("button", { name: "Frage stellen" }));
    expect(await screen.findByRole("heading", { name: "Klärung erforderlich" })).toBeVisible();
  });
});
