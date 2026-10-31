import { api } from "../api/client";
import { formatUploadLimit, MAX_UPLOAD_BYTES } from "../lib/uploads";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";
import * as declarationApi from "../api/endpoints";
import type { DeclarationDocument, DeclarationImport, Property, Unit } from "../api/types";
import { DeclarationImportPage } from "./DeclarationImportPage";
import { PropertiesPage } from "./PropertiesPage";
import { PropertyDetailPage } from "./PropertyDetailPage";
import { AuthProvider } from "../auth/AuthContext";

import {
  createDeclarationImport,
  declarationImportDocumentContentUrl,
  pollDeclarationImport,
  propertyDocumentContentUrl,
  reviewDeclarationCandidate,
  uploadDeclarationDocument,
} from "../api/endpoints";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function declarationImport(status: DeclarationImport["status"]): DeclarationImport {
  return {
    id: "import-1",
    mode: "NEW_PROPERTY",
    target_property_id: null,
    applied_property_id: null,
    status,
    draft_revision: 1,
    progress_percent: 50,
    current_stage: null,
    error_code: null,
    error_message: null,
    input_tokens: 0,
    output_tokens: 0,
    estimated_cost_usd: "0.000000",
    attempt_count: 1,
    max_cost_usd: "2.000000",
  };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("declaration import API contract", () => {
  it("creates an import with its mode and an optional target property", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse(declarationImport("DRAFT"))),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createDeclarationImport({ mode: "EXISTING_PROPERTY", target_property_id: "property-1" });
    await createDeclarationImport({ mode: "NEW_PROPERTY" });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/declaration-imports",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mode: "EXISTING_PROPERTY", target_property_id: "property-1" }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/declaration-imports",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mode: "NEW_PROPERTY" }),
      }),
    );
  });

  it("uploads declaration PDFs as multipart without an explicit Content-Type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: "document-1" }));
    vi.stubGlobal("fetch", fetchMock);
    const formData = new FormData();
    formData.append("file", new File(["%PDF"], "declaration.pdf", { type: "application/pdf" }));
    formData.append("document_role", "DECLARATION");
    formData.append("sequence_no", "1");

    await uploadDeclarationDocument("import-1", formData);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBe(formData);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("sends scalar corrections accepted by the field-review API", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ draft_revision: 2, candidate: null, candidates: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await reviewDeclarationCandidate("import-1", "candidate-1", {
      draft_revision: 1,
      decision: "CORRECTED",
      corrected_value: "Talstraße 112",
    }, "review-key");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/declaration-imports/import-1/candidates/candidate-1",
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "Idempotency-Key": "review-key" }),
        body: JSON.stringify({
          draft_revision: 1,
          decision: "CORRECTED",
          corrected_value: "Talstraße 112",
        }),
      }),
    );
  });

  it("keeps property-document PDF pages in a client-side fragment", () => {
    expect(propertyDocumentContentUrl("property-1", "document-1", 7)).toBe(
      "/api/properties/property-1/documents/document-1/content#page=7",
    );
  });

  it("keeps draft-document PDF pages in a client-side fragment", () => {
    expect(declarationImportDocumentContentUrl("import-1", "document-1", 4)).toBe(
      "/api/declaration-imports/import-1/documents/document-1/content#page=4",
    );
  });

  it.each(["NEEDS_REVIEW", "FAILED", "CANCELLED", "APPLIED"])(
    "stops polling when the import reaches %s",
    async (terminalStatus) => {
      vi.useFakeTimers();
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(declarationImport(terminalStatus as DeclarationImport["status"])));
      vi.stubGlobal("fetch", fetchMock);
      const onUpdate = vi.fn();
      const onError = vi.fn();

      const stop = pollDeclarationImport("import-1", {
        intervalMs: 10,
        onUpdate,
        onError,
      });
      await vi.runAllTimersAsync();

      expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ status: terminalStatus }));
      expect(onError).not.toHaveBeenCalled();
      expect(fetchMock).toHaveBeenCalledTimes(1);

      stop();
      await vi.runAllTimersAsync();
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("reports a polling error once without scheduling a retry", async () => {
    vi.useFakeTimers();
    const error = new Error("network unavailable");
    const fetchMock = vi.fn().mockRejectedValue(error);
    vi.stubGlobal("fetch", fetchMock);
    const onUpdate = vi.fn();
    const onError = vi.fn();

    pollDeclarationImport("import-1", { intervalMs: 10, onUpdate, onError });
    await vi.runAllTimersAsync();

    expect(onUpdate).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(error);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not schedule another poll when cancelled by a non-terminal update callback", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse(declarationImport("QUEUED"))),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onError = vi.fn();
    let stop: () => void = () => undefined;
    const onUpdate = vi.fn(() => stop());

    stop = pollDeclarationImport("import-1", { intervalMs: 10, onUpdate, onError });
    await vi.runAllTimersAsync();

    expect(onUpdate).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("routes update callback errors through onError without scheduling another poll", async () => {
    vi.useFakeTimers();
    const error = new Error("consumer update failed");
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse(declarationImport("QUEUED"))),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onUpdate = vi.fn(() => {
      throw error;
    });
    const onError = vi.fn();

    pollDeclarationImport("import-1", { intervalMs: 10, onUpdate, onError });
    await vi.runAllTimersAsync();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(error);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

function renderImportRoute(route = "/teilungserklaerung/import") {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/teilungserklaerung/import" element={<DeclarationImportPage />} />
        <Route path="/objekte/:propertyId/teilungserklaerung/import" element={<DeclarationImportPage />} />
        <Route path="/teilungserklaerung/import/:importId" element={<DeclarationImportPage />} />
        <Route path="/teilungserklaerung/import/:importId/review" element={<DeclarationImportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function pendingImport(overrides: Partial<ReturnType<typeof declarationImport>> = {}) {
  return { ...declarationImport("DRAFT"), ...overrides };
}

describe("Teilungserklärung upload wizard", () => {
  it("defaults to a new-property import and discloses OpenAI processing", async () => {
    vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(pendingImport());
    renderImportRoute();

    expect(await screen.findByText("Neues Objekt aus einer Teilungserklärung anlegen")).toBeVisible();
    expect(screen.getByText(/über die OpenAI API verarbeitet/i)).toBeVisible();
    expect(declarationApi.createDeclarationImport).toHaveBeenCalledWith({ mode: "NEW_PROPERTY" });
  });

  it("locks the target property for an amendment import", async () => {
    vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(
      pendingImport({ mode: "EXISTING_PROPERTY", target_property_id: "property-42" }),
    );
    renderImportRoute("/objekte/property-42/teilungserklaerung/import");

    expect(await screen.findByText("Nachtrag für das aktuelle Objekt einlesen")).toBeVisible();
    expect(screen.getByText(/Objekt-ID: property-42/)).toBeVisible();
    expect(declarationApi.createDeclarationImport).toHaveBeenCalledWith({
      mode: "EXISTING_PROPERTY",
      target_property_id: "property-42",
    });
  });

  it("rejects non-PDFs and too-large files before upload", async () => {
    vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(pendingImport());
    const uploadSpy = vi.spyOn(declarationApi, "uploadDeclarationDocument");
    renderImportRoute();
    await screen.findByLabelText("PDF-Dateien auswählen");

    const input = screen.getByLabelText("PDF-Dateien auswählen");
    const tooLarge = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], "gross.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, {
      target: { files: [
        new File(["not a pdf"], "notiz.txt", { type: "text/plain" }),
        new File(["wrong mime"], "umbenannt.pdf", { type: "text/plain" }),
        tooLarge,
      ] },
    });

    expect(await screen.findByText(/nur PDF-Dateien/i)).toBeVisible();
    expect(screen.getByText(/„umbenannt\.pdf“ ist keine PDF-Datei/i)).toBeVisible();
    expect(screen.getByText((text) => text.includes(`„gross.pdf“ ist größer als maximal ${formatUploadLimit(MAX_UPLOAD_BYTES)}`))).toBeVisible();
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  it("uses the backend metadata cap for file selection boundaries", async () => {
    const metadata = { max_upload_bytes: 1_294_537 };
    const formattedLimit = formatUploadLimit(metadata.max_upload_bytes);
    const get = api.get;
    vi.spyOn(api, "get").mockImplementation((url, options) => url === "/api/meta"
      ? Promise.resolve(metadata) : get(url, options));
    vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(pendingImport());
    renderImportRoute();
    const input = await screen.findByLabelText("PDF-Dateien auswählen");
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/meta", { cache: "no-store" }));
    expect(await screen.findByText(`Erlaubt sind PDFs bis maximal ${formattedLimit} pro Datei. Die serverseitige Prüfung bleibt maßgeblich.`)).toBeVisible();
    fireEvent.change(input, { target: { files: [
      Object.defineProperty(new File(["%PDF"], "exakt.pdf", { type: "application/pdf" }), "size", { value: metadata.max_upload_bytes }),
      Object.defineProperty(new File(["%PDF-"], "darueber.pdf", { type: "application/pdf" }), "size", { value: metadata.max_upload_bytes + 1 }),
    ] } });
    expect(screen.getByDisplayValue("exakt.pdf")).toBeVisible();
    expect(screen.getByText(`„darueber.pdf“ ist größer als maximal ${formattedLimit}.`)).toBeVisible();
    expect(screen.queryByDisplayValue("darueber.pdf")).not.toBeInTheDocument();
  });

  it("uploads multiple PDFs, lets their metadata be changed, and gates start", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(pendingImport());
    const uploadSpy = vi.spyOn(declarationApi, "uploadDeclarationDocument")
      .mockResolvedValueOnce({
        id: "document-1", property_id: null, doc_type: "DECLARATION", title: "eins.pdf",
        original_filename: "eins.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "a", sequence_no: 1, document_role: "DECLARATION",
      })
      .mockResolvedValueOnce({
        id: "document-2", property_id: null, doc_type: "DECLARATION", title: "zwei.pdf",
        original_filename: "zwei.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "b", sequence_no: 2, document_role: "DECLARATION",
      });
    vi.spyOn(declarationApi, "updateDeclarationDocument").mockImplementation(async (_importId, _documentId, body) => ({
      id: _documentId, property_id: null, doc_type: body.document_role ?? "DECLARATION",
      title: body.title ?? "Dokument", original_filename: "Dokument.pdf", mime_type: "application/pdf",
      content_byte_size: 5, content_sha256: "a", sequence_no: body.sequence_no ?? 1,
      document_role: body.document_role ?? "DECLARATION",
    }));
    renderImportRoute();
    const input = await screen.findByLabelText("PDF-Dateien auswählen");

    expect(screen.getByRole("button", { name: "Import starten" })).toBeDisabled();
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, {
      target: { files: [
        new File(["%PDF"], "eins.pdf", { type: "application/pdf" }),
        new File(["%PDF"], "zwei.pdf", { type: "application/pdf" }),
      ] },
    });

    expect(await screen.findByDisplayValue("eins.pdf")).toBeVisible();
    expect(await screen.findByDisplayValue("zwei.pdf")).toBeVisible();
    expect(screen.getByRole("button", { name: "Import starten" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Ausgewählte PDFs hochladen" }));
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(2));
    const firstUpload = uploadSpy.mock.calls[0][1];
    const secondUpload = uploadSpy.mock.calls[1][1];
    expect(firstUpload.get("document_role")).toBe("DECLARATION");
    expect(firstUpload.get("sequence_no")).toBe("1");
    expect(secondUpload.get("sequence_no")).toBe("2");
    expect(screen.getByRole("button", { name: "Import starten" })).toBeEnabled();
    fireEvent.change(screen.getAllByLabelText("Dokumenttyp")[0], { target: { value: "AMENDMENT" } });
    fireEvent.click(screen.getByRole("button", { name: "eins.pdf nach unten verschieben" }));

    await waitFor(() => expect(declarationApi.updateDeclarationDocument).toHaveBeenCalled());
  });

  it("loads a durable import, polls every five seconds, and enters the readiness state", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(
      pendingImport({ mode: "EXISTING_PROPERTY", target_property_id: "property-42", status: "QUEUED", progress_percent: 0 }),
    );
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    const pollSpy = vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation((_id, options) => {
      expect(options.intervalMs).toBe(5_000);
      options.onUpdate(pendingImport({ mode: "EXISTING_PROPERTY", target_property_id: "property-42", status: "NEEDS_REVIEW", progress_percent: 100 }));
      return () => undefined;
    });
    renderImportRoute("/teilungserklaerung/import/import-1");

    expect(await screen.findByText("Bereit für die fachliche Prüfung")).toBeVisible();
    expect(screen.getByText(/Objekt-ID: property-42/)).toBeVisible();
    expect(pollSpy).toHaveBeenCalledWith("import-1", expect.objectContaining({ intervalMs: 5_000 }));
  });

  it("shows Retry only for retryable failure codes", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(
      pendingImport({ status: "FAILED", error_code: "PROVIDER_TIMEOUT", error_message: "timeout" }),
    );
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    const pollSpy = vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    vi.spyOn(declarationApi, "retryDeclarationImport").mockResolvedValue(pendingImport({ status: "QUEUED" }));
    renderImportRoute("/teilungserklaerung/import/import-1");

    expect(await screen.findByText(/Zeitüberschreitung beim Dienstleister/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(declarationApi.retryDeclarationImport).toHaveBeenCalledWith("import-1", expect.any(String)));
    await waitFor(() => expect(pollSpy).toHaveBeenCalledTimes(2));
  });

  it("explains the hosted monthly budget refusal in German", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(
      pendingImport({ status: "FAILED", error_code: "ORGANIZATION_AI_BUDGET_EXCEEDED", error_message: "ORGANIZATION_AI_BUDGET_EXCEEDED" }),
    );
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    renderImportRoute("/teilungserklaerung/import/import-1");
    expect(await screen.findByText(/Das monatliche KI-Budget ist ausgeschöpft/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
  });

  it("restores uploaded documents from a draft URL and continues at the highest sequence", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([
      {
        id: "document-2", property_id: null, doc_type: "AMENDMENT", title: "Nachtrag.pdf",
        original_filename: "Nachtrag.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "b", sequence_no: 2, document_role: "AMENDMENT",
      },
    ]);
    const uploadSpy = vi.spyOn(declarationApi, "uploadDeclarationDocument").mockResolvedValue({
      id: "document-3", property_id: null, doc_type: "DECLARATION", title: "neu.pdf",
      original_filename: "neu.pdf", mime_type: "application/pdf", content_byte_size: 5,
      content_sha256: "c", sequence_no: 3, document_role: "DECLARATION",
    });
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    renderImportRoute("/teilungserklaerung/import/import-1");

    expect(await screen.findByDisplayValue("Nachtrag.pdf")).toBeVisible();
    expect(screen.getByRole("button", { name: "Import starten" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("PDF-Dateien auswählen"), {
      target: { files: [new File(["%PDF"], "neu.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ausgewählte PDFs hochladen" }));
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));
    expect(uploadSpy.mock.calls[0][1].get("sequence_no")).toBe("3");
  });

  it("accepts an empty PDF MIME type but prevents duplicate upload batches", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    const uploadSpy = vi.spyOn(declarationApi, "uploadDeclarationDocument").mockResolvedValue({
      id: "document-1", property_id: null, doc_type: "DECLARATION", title: "leer.pdf",
      original_filename: "leer.pdf", mime_type: "application/pdf", content_byte_size: 5,
      content_sha256: "a", sequence_no: 1, document_role: "DECLARATION",
    });
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    renderImportRoute("/teilungserklaerung/import/import-1");
    await screen.findByLabelText("PDF-Dateien auswählen");

    fireEvent.change(screen.getByLabelText("PDF-Dateien auswählen"), {
      target: { files: [new File(["%PDF"], "leer.pdf", { type: "" })] },
    });
    const upload = screen.getByRole("button", { name: "Ausgewählte PDFs hochladen" });
    fireEvent.click(upload);
    fireEvent.click(upload);
    await waitFor(() => expect(uploadSpy).toHaveBeenCalledTimes(1));
    const uploadedFile = uploadSpy.mock.calls[0][1].get("file");
    expect(uploadedFile).toBeInstanceOf(File);
    expect((uploadedFile as File).type).toBe("application/pdf");
  });

  it("serializes document reordering while the collision-safe update is running", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([
      {
        id: "document-1", property_id: null, doc_type: "DECLARATION", title: "eins.pdf",
        original_filename: "eins.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "a", sequence_no: 1, document_role: "DECLARATION",
      },
      {
        id: "document-2", property_id: null, doc_type: "AMENDMENT", title: "zwei.pdf",
        original_filename: "zwei.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "b", sequence_no: 2, document_role: "AMENDMENT",
      },
    ]);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    let resolveUpdate: ((value: Awaited<ReturnType<typeof declarationApi.updateDeclarationDocument>>) => void) | undefined;
    const updateSpy = vi.spyOn(declarationApi, "updateDeclarationDocument").mockImplementation(() => new Promise((resolve) => {
      resolveUpdate = resolve;
    }));
    renderImportRoute("/teilungserklaerung/import/import-1");
    const down = await screen.findByRole("button", { name: "eins.pdf nach unten verschieben" });

    fireEvent.click(down);
    expect(down).toBeDisabled();
    fireEvent.click(down);
    expect(updateSpy).toHaveBeenCalledTimes(1);
    resolveUpdate?.({
      id: "document-2", property_id: null, doc_type: "AMENDMENT", title: "zwei.pdf",
      original_filename: "zwei.pdf", mime_type: "application/pdf", content_byte_size: 5,
      content_sha256: "b", sequence_no: 4, document_role: "AMENDMENT",
    });
  });

  it("reloads the persisted order after a reorder request fails", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
    const persistedDocuments = [
      {
        id: "document-1", property_id: null, doc_type: "DECLARATION" as const, title: "eins.pdf",
        original_filename: "eins.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "a", sequence_no: 1, document_role: "DECLARATION" as const,
      },
      {
        id: "document-2", property_id: null, doc_type: "AMENDMENT" as const, title: "zwei.pdf",
        original_filename: "zwei.pdf", mime_type: "application/pdf", content_byte_size: 5,
        content_sha256: "b", sequence_no: 2, document_role: "AMENDMENT" as const,
      },
    ];
    const listSpy = vi.spyOn(declarationApi, "listDeclarationDocuments")
      .mockResolvedValueOnce(persistedDocuments)
      .mockResolvedValueOnce(persistedDocuments);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    vi.spyOn(declarationApi, "updateDeclarationDocument").mockRejectedValue(new Error("network"));
    renderImportRoute("/teilungserklaerung/import/import-1");
    const down = await screen.findByRole("button", { name: "eins.pdf nach unten verschieben" });

    fireEvent.click(down);

    expect(await screen.findByRole("alert")).toHaveTextContent(/gespeicherte Reihenfolge wurde neu geladen/i);
    expect(listSpy).toHaveBeenCalledTimes(2);
    expect(screen.getAllByLabelText("Dokumenttitel").map((input) => (input as HTMLInputElement).value)).toEqual([
      "eins.pdf",
      "zwei.pdf",
    ]);
  });

  it("does not offer Retry for a non-retryable import failure", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(
      pendingImport({ status: "FAILED", error_code: "COST_LIMIT", error_message: "Kostenlimit" }),
    );
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    renderImportRoute("/teilungserklaerung/import/import-1");

    expect(await screen.findByRole("alert")).toHaveTextContent(/Verarbeitung fehlgeschlagen/i);
    expect(screen.queryByRole("button", { name: "Erneut versuchen" })).not.toBeInTheDocument();
  });
});

const exampleProperty: Property = {
  id: "property-1", name: "Talstraße 112", street: "Talstraße 112", zip_code: "40217", city: "Düsseldorf",
  management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1_000_000, mea_scale_status: "CONFIRMED",
};

const exampleUnit: Unit = {
  id: "unit-1", property_id: "property-1", number: "1", location: "EG links", area: null, heated_area: null,
  usage_type: "WOHNUNG", mea: "95.464", building_part: "Haus A",
};

describe("Teilungserklärung entry points and legal shares", () => {
  function renderWithRole(children: ReactNode, role = "CLERK") {
    vi.spyOn(declarationApi, "getCurrentSession").mockResolvedValue({
      user_id: "user-1",
      organization_id: "org-1",
      role,
    });
    vi.spyOn(declarationApi, "getMyOrganization").mockResolvedValue({ id: "org-1", name: "Test" });
    return render(<AuthProvider>{children}</AuthProvider>);
  }

  it("offers a new-property import from the property list", async () => {
    vi.spyOn(declarationApi, "listProperties").mockResolvedValue([]);
    renderWithRole(
      <MemoryRouter><PropertiesPage /></MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "Teilungserklärung importieren" })).toHaveAttribute(
      "href", "/teilungserklaerung/import",
    );
  });

  it("shows the property import entry, building part, and exact legal MEA", async () => {
    vi.spyOn(declarationApi, "getProperty").mockResolvedValue(exampleProperty);
    vi.spyOn(declarationApi, "listUnits").mockResolvedValue([exampleUnit]);
    vi.spyOn(declarationApi, "getPropertyKnowledge").mockResolvedValue({ property_id: "property-1", documents: [{ id: "doc-1", doc_type: "DECLARATION", title: "TE", document_date: null, deed_reference: null, page_count: 1, archived_at: null }], document_relations: [], rules_by_topic: { HEATING: [] }, open_rule_topics: [] });
    const updateUnitSpy = vi.spyOn(declarationApi, "updateUnit").mockResolvedValue(exampleUnit);
    renderWithRole(
      <MemoryRouter initialEntries={["/objekte/property-1"]}>
        <Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("95.464 / 1.000.000")).toBeVisible();
    expect(screen.getByText("Haus A")).toBeVisible();
    expect(screen.getByRole("link", { name: "Nachtrag oder Teilungserklärung einlesen" })).toHaveAttribute(
      "href", "/objekte/property-1/teilungserklaerung/import",
    );
    expect(screen.getByText("1 Dokumente · 0 Regeln")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByLabelText("Gebäudeteil")).toBeVisible();
    fireEvent.change(screen.getByLabelText("Gebäudeteil"), { target: { value: "" } });
    fireEvent.submit(screen.getByRole("button", { name: "Speichern" }).closest("form")!);
    await waitFor(() => expect(updateUnitSpy).toHaveBeenCalledWith("unit-1", expect.objectContaining({ building_part: "" })));
  });

  it("does not expose property write actions to read-only users", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [exampleProperty], next_cursor: null });
    renderWithRole(<MemoryRouter><PropertiesPage /></MemoryRouter>, "READ_ONLY");
    expect(await screen.findByRole("link", { name: "Talstraße 112" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Teilungserklärung importieren" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
  });

  it("keeps the detail knowledge link but hides import and unit editing for read-only users", async () => {
    vi.spyOn(declarationApi, "getProperty").mockResolvedValue(exampleProperty);
    vi.spyOn(declarationApi, "listUnits").mockResolvedValue([exampleUnit]);
    vi.spyOn(declarationApi, "getPropertyKnowledge").mockResolvedValue({ property_id: "property-1", documents: [], document_relations: [], rules_by_topic: {}, open_rule_topics: [] });
    renderWithRole(<MemoryRouter initialEntries={["/objekte/property-1"]}><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes></MemoryRouter>, "READ_ONLY");

    expect(await screen.findByRole("link", { name: "Dokumente & Wissen öffnen" })).toHaveAttribute("href", "/objekte/property-1/wissen");
    expect(screen.queryByRole("link", { name: "Nachtrag oder Teilungserklärung einlesen" })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Neue Einheit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
  });
});


describe("durable declaration queue commands", () => {
  it("retains the retry key after a lost response and explains permission cancellation", async () => {
    vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(
      pendingImport({ status: "CANCELLED", error_code: "JOB_PERMISSION_CHANGED" }),
    );
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue([]);
    vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
    const retry = vi.spyOn(declarationApi, "retryDeclarationImport")
      .mockRejectedValueOnce(new Error("lost response"))
      .mockResolvedValueOnce(pendingImport({ status: "QUEUED" }));
    renderImportRoute("/teilungserklaerung/import/import-1");
    expect(await screen.findByText(/Berechtigung des auslösenden Benutzers/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    await waitFor(() => expect(retry).toHaveBeenCalledTimes(2));
    expect(retry.mock.calls[0][1]).toMatch(/^[0-9a-f-]{36}$/);
    expect(retry.mock.calls[1]).toEqual(retry.mock.calls[0]);
  });
});

it("waits for the persisted draft documents before accepting upload selections", async () => {
  vi.spyOn(declarationApi, "createDeclarationImport").mockResolvedValue(pendingImport());
  vi.spyOn(declarationApi, "getDeclarationImport").mockResolvedValue(pendingImport());
  let finish!: (documents: DeclarationDocument[]) => void;
  vi.spyOn(declarationApi, "listDeclarationDocuments").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  vi.spyOn(declarationApi, "pollDeclarationImport").mockImplementation(() => () => undefined);
  renderImportRoute();
  await waitFor(() => expect(declarationApi.listDeclarationDocuments).toHaveBeenCalledWith("import-1"));
  expect(screen.getByLabelText("PDF-Dateien auswählen", { exact: true })).toBeDisabled();
  finish([]);
  await waitFor(() => expect(screen.getByLabelText("PDF-Dateien auswählen", { exact: true })).toBeEnabled());
});
