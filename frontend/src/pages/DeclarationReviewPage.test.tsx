import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import * as declarationApi from "../api/endpoints";
import { ApiError } from "../api/client";
import type { ApprovalPreview, DeclarationCandidate, DeclarationDocument } from "../api/types";
import { DeclarationReviewPage } from "./DeclarationReviewPage";
import * as auth from "../auth/useAuth";

const documents: DeclarationDocument[] = [{
  id: "document-1", property_id: null, doc_type: "DECLARATION", document_role: "DECLARATION",
  title: "Teilungserklärung Watelerstraße", original_filename: "teilung.pdf", mime_type: "application/pdf",
  content_byte_size: 10, content_sha256: "sha", sequence_no: 1,
}];

function candidate(overrides: Partial<DeclarationCandidate> = {}): DeclarationCandidate {
  return {
    id: "candidate-1", stable_key: "property:street", candidate_type: "PROPERTY_FIELD", subject_key: "property",
    field_key: "street", operation: "CREATE", previous_value_json: null,
    proposed_value_json: { value: "Watelerstraße 12" }, corrected_value_json: null,
    confidence: "0.98", risk_class: "LOW", requires_individual_review: false, review_status: "PENDING",
    review_comment: null,
    evidence: [{ id: "evidence-1", document_id: "document-1", field_path: "address.street", pdf_page: 5, printed_page: "3", section_ref: "§ 1", quote: "Watelerstraße 12" }],
    issues: [],
    ...overrides,
  };
}

const approveablePreview: ApprovalPreview = {
  import_id: "import-1", draft_revision: 3, can_approve: true, issues: [], pending_candidate_ids: [],
  import_mode: "NEW_PROPERTY",
};

function mockReviewData(items: DeclarationCandidate[] = [candidate()], preview = approveablePreview) {
  vi.spyOn(declarationApi, "listDeclarationCandidates").mockResolvedValue(items);
  vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValue(documents);
  vi.spyOn(declarationApi, "getDeclarationApprovalPreview").mockResolvedValue(preview);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function RouteSwitchButton() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate("/teilungserklaerung/import/import-2/review")}>Zu Import B wechseln</button>;
}

function renderReview(withRouteSwitch = false) {
  vi.spyOn(auth, "useAuth").mockReturnValue({ loading: false, session: { user_id: "publisher", organization_id: "org", role: "OWNER_ADMIN" }, login: vi.fn(), logout: vi.fn() });
  return render(
    <MemoryRouter initialEntries={["/teilungserklaerung/import/import-1/review"]}>
      <Routes>
        <Route path="/teilungserklaerung/import/:importId/review" element={<>{withRouteSwitch && <RouteSwitchButton />}<DeclarationReviewPage /></>} />
        <Route path="/objekte/:propertyId" element={<p>Objekt property-canonical wurde übernommen.</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Teilungserklärung review", () => {
  it("requires an explicit audience choice and reason, then retries the exact submitted grant", async () => {
    mockReviewData([candidate({ review_status: "ACCEPTED" })]);
    const approve = vi.spyOn(declarationApi, "approveDeclarationImport").mockRejectedValue(new Error("Antwort verloren"));
    vi.stubGlobal("crypto", { randomUUID: () => "audience-command-key" });
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Übernahme vorbereiten" }));
    const choice = screen.getByRole("checkbox", { name: /Für mich Lesen/ });
    expect(choice).not.toBeChecked();
    fireEvent.click(choice);
    expect(screen.getByRole("button", { name: "Endgültig übernehmen" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "Objektfreigabe geprüft" } });
    fireEvent.click(screen.getByRole("button", { name: "Endgültig übernehmen" }));
    expect(await screen.findByText("Antwort verloren")).toBeVisible();
    expect(screen.getByLabelText("Begründung")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Endgültig übernehmen" }));
    await waitFor(() => expect(approve).toHaveBeenCalledTimes(2));
    expect(approve.mock.calls[0]).toEqual(approve.mock.calls[1]);
    expect(approve.mock.calls[0][1]).toEqual({
      draft_revision: 3, idempotency_key: "audience-command-key",
      new_property_audience: { label: "Gebäudedaten", reason: "Objektfreigabe geprüft", grants: [
        { user_id: "publisher", capabilities: ["read", "prepare", "approve", "export"], reason: "Objektfreigabe geprüft" },
      ] },
    });
    expect(screen.getByText(/Zugriffsbeschränkung ist noch nicht aktiviert/)).toBeVisible();
  });

  it("does not offer new audience creation for an existing property amendment", async () => {
    mockReviewData([candidate()], { ...approveablePreview, import_mode: "EXISTING_PROPERTY" });
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Übernahme vorbereiten" }));
    expect(screen.queryByRole("checkbox", { name: /Für mich Lesen/ })).not.toBeInTheDocument();
  });

  it("groups candidates and renders source-first review metadata", async () => {
    mockReviewData([
      candidate(),
      candidate({ id: "unit", candidate_type: "UNIT_FIELD", subject_key: "unit:1" }),
      candidate({ id: "assignment", candidate_type: "ASSIGNMENT" }),
      candidate({ id: "rule", candidate_type: "RULE", proposed_value_json: {
        subject_key: "rule:heating", title: "Heizkosten", topic: "heating", summary: "Verteilung der Heizkosten",
        scope: { all_units: true, unit_subject_keys: [], building_parts: [], participant_groups: ["Eigentümer"], additional_scope: [] },
        effect: {
          obligation: "Abrechnung nach Verbrauch", entitlement: "Einsicht in Abrechnung", consequence: "Nachzahlung",
          administrative_note: "Jährlich prüfen", responsibility: ["WEG-Verwaltung"], exceptions: ["Leerstand"],
          allocation_formula: { declared_complete: true, parts: [{ label: "Verbrauch", basis: "Messwerte", share: { kind: "PERCENTAGE", value: "70" }, applies_to: ["all"] }] },
        },
        proposed_status: "EFFECTIVE", supersedes_subject_keys: ["rule:old"], conflicts_with_subject_keys: ["rule:conflict"],
      } }),
      candidate({ id: "clarify", candidate_type: "PROPERTY_FIELD", issues: [{ id: "issue", code: "CONFLICT", severity: "WARNING", message: "Abweichende Angaben", resolution_status: "OPEN", candidate_id: "clarify" }] }),
    ]);
    renderReview();

    expect(await screen.findByRole("heading", { name: "Stammdaten" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Einheiten" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Zuordnungen" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Regeln" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Klärungsbedarf" })).toBeVisible();
    expect(screen.getAllByText("Vorschlag").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Quellen: 1").length).toBeGreaterThan(0);
    expect(screen.getByText("Prüfen: Abweichende Angaben")).toBeVisible();
    expect(screen.getByText("Verantwortung")).toBeVisible();
    expect(screen.getByText("Verteilungsformel")).toBeVisible();
    expect(screen.getByText("Gültigkeit")).toBeVisible();
    expect(screen.getByText("Ablösungen")).toBeVisible();
    expect(screen.getByText("Konflikte")).toBeVisible();
  });

  it("opens the authorized source at its one-based page with the document title", async () => {
    mockReviewData();
    renderReview();

    await screen.findByRole("button", { name: /quelle auf seite 5: teilungserklärung watelerstraße/i });
    fireEvent.click(screen.getByRole("button", { name: /quelle auf seite 5/i }));

    expect(screen.getByTitle("Originalquelle: Teilungserklärung Watelerstraße")).toHaveAttribute(
      "src", "/api/declaration-imports/import-1/documents/document-1/content#page=5",
    );
  });

  it("never exposes bulk selection for individually reviewed or policy-sensitive candidates", async () => {
    mockReviewData([
      candidate({ id: "safe" }),
      candidate({ id: "update", operation: "UPDATE" }),
      candidate({ id: "rule", candidate_type: "RULE" }),
      candidate({ id: "relation", candidate_type: "DOCUMENT_RELATION" }),
      candidate({ id: "scale", candidate_type: "MEA_RESCALE" }),
      candidate({ id: "low-confidence", confidence: "0.62" }),
      candidate({ id: "warning", issues: [{ id: "warn", code: "warn", severity: "WARNING", message: "Warnung", resolution_status: "OPEN", candidate_id: "warning" }] }),
      candidate({ id: "individual", requires_individual_review: true }),
      candidate({ id: "accepted", review_status: "ACCEPTED" }),
      candidate({ id: "corrected", review_status: "CORRECTED" }),
      candidate({ id: "rejected", review_status: "REJECTED" }),
    ]);
    renderReview();

    await screen.findByText("Stammdaten");
    expect(screen.getAllByRole("checkbox", { name: /für sammelfreigabe auswählen/i })).toHaveLength(1);
    expect(screen.getByLabelText("Prüfstatus: CORRECTED")).toBeVisible();
    expect(screen.getByRole("button", { name: /sichere vorschläge akzeptieren/i })).toBeEnabled();
  });

  it("bulk accepts only selected safe candidates through the server-authoritative endpoint", async () => {
    const safe = candidate({ id: "safe" });
    mockReviewData([safe]);
    const bulk = vi.spyOn(declarationApi, "bulkReviewDeclarationCandidates").mockResolvedValue({
      draft_revision: 4, candidate: null, candidates: [safe],
    });
    renderReview();

    fireEvent.click(await screen.findByRole("checkbox", { name: /für sammelfreigabe auswählen/i }));
    fireEvent.click(screen.getByRole("button", { name: /sichere vorschläge akzeptieren/i }));

    await waitFor(() => expect(bulk).toHaveBeenCalledWith("import-1", {
      draft_revision: 3, candidate_ids: ["safe"], decision: "ACCEPTED",
    }, expect.any(String)));
  });

  it("reuses the single-review key after a lost response and replaces it when input changes", async () => {
    mockReviewData([candidate()]);
    const review = vi.spyOn(declarationApi, "reviewDeclarationCandidate").mockRejectedValue(new Error("Antwort verloren"));
    const randomUUID = vi.fn().mockReturnValueOnce("review-key-1").mockReturnValueOnce("review-key-2");
    vi.stubGlobal("crypto", { randomUUID });
    renderReview();
    fireEvent.click(await screen.findByRole("button", { name: "Akzeptieren" }));
    await screen.findByText("Antwort verloren");
    fireEvent.click(screen.getByRole("button", { name: "Akzeptieren" }));
    await waitFor(() => expect(review).toHaveBeenCalledTimes(2));
    expect(review.mock.calls[0]).toEqual(review.mock.calls[1]);
    expect(review.mock.calls[1][3]).toBe("review-key-1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Akzeptieren" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Kommentar"), { target: { value: "Geprüft" } });
    fireEvent.click(screen.getByRole("button", { name: "Akzeptieren" }));
    await waitFor(() => expect(review).toHaveBeenCalledTimes(3));
    expect(review.mock.calls[2][3]).toBe("review-key-2");
  });

  it("reuses the bulk-review key after a lost response", async () => {
    mockReviewData([candidate({ id: "safe" })]);
    const bulk = vi.spyOn(declarationApi, "bulkReviewDeclarationCandidates").mockRejectedValue(new Error("Antwort verloren"));
    vi.stubGlobal("crypto", { randomUUID: () => "bulk-key" });
    renderReview();
    fireEvent.click(await screen.findByRole("checkbox", { name: /für sammelfreigabe auswählen/i }));
    fireEvent.click(screen.getByRole("button", { name: /sichere vorschläge akzeptieren/i }));
    await screen.findByText("Antwort verloren");
    fireEvent.click(screen.getByRole("button", { name: /sichere vorschläge akzeptieren/i }));
    await waitFor(() => expect(bulk).toHaveBeenCalledTimes(2));
    expect(bulk.mock.calls[0]).toEqual(bulk.mock.calls[1]);
    expect(bulk.mock.calls[1][2]).toBe("bulk-key");
  });

  it("submits a correction as parsed JSON, not as a display string", async () => {
    const mea = candidate({
      candidate_type: "UNIT_FIELD", field_key: "mea", proposed_value_json: { kind: "MEA", numerator: 17, denominator: 1000 },
      requires_individual_review: true,
    });
    mockReviewData([mea]);
    const review = vi.spyOn(declarationApi, "reviewDeclarationCandidate").mockResolvedValue({ draft_revision: 4, candidate: null, candidates: [mea] });
    renderReview();

    const correction = await screen.findByLabelText("Korrigierter JSON-Wert");
    fireEvent.change(correction, { target: { value: '{"kind":"MEA","numerator":18,"denominator":1000}' } });
    fireEvent.click(screen.getByRole("button", { name: "Korrektur speichern" }));

    await waitFor(() => expect(review).toHaveBeenCalledWith("import-1", "candidate-1", {
      draft_revision: 3, decision: "CORRECTED", corrected_value: { kind: "MEA", numerator: 18, denominator: 1000 },
    }, expect.any(String)));
  });

  it("retains an unsent MEA correction during and after a delayed stale reload", async () => {
    const mea = candidate({ candidate_type: "UNIT_FIELD", field_key: "mea", requires_individual_review: true, proposed_value_json: { kind: "MEA", numerator: 17, denominator: 1000 } });
    const delayedCandidates = deferred<DeclarationCandidate[]>();
    const delayedDocuments = deferred<DeclarationDocument[]>();
    const delayedPreview = deferred<ApprovalPreview>();
    vi.spyOn(declarationApi, "listDeclarationCandidates").mockResolvedValueOnce([mea]).mockImplementationOnce(() => delayedCandidates.promise);
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockResolvedValueOnce(documents).mockImplementationOnce(() => delayedDocuments.promise);
    vi.spyOn(declarationApi, "getDeclarationApprovalPreview").mockResolvedValueOnce(approveablePreview).mockImplementationOnce(() => delayedPreview.promise);
    vi.spyOn(declarationApi, "reviewDeclarationCandidate").mockRejectedValue(new ApiError(409, "STALE_DRAFT_REVISION"));
    renderReview();

    const correction = await screen.findByLabelText("Korrigierter JSON-Wert");
    fireEvent.change(correction, { target: { value: '{"kind":"MEA","numerator":19,"denominator":1000}' } });
    fireEvent.click(screen.getByRole("button", { name: "Korrektur speichern" }));

    await waitFor(() => expect(declarationApi.listDeclarationCandidates).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("Korrigierter JSON-Wert")).toHaveValue('{"kind":"MEA","numerator":19,"denominator":1000}');
    delayedCandidates.resolve([mea]);
    delayedDocuments.resolve(documents);
    delayedPreview.resolve(approveablePreview);
    expect(await screen.findByText(/parallel geändert/i)).toBeVisible();
    expect(screen.getByLabelText("Korrigierter JSON-Wert")).toHaveValue('{"kind":"MEA","numerator":19,"denominator":1000}');
    expect(declarationApi.listDeclarationCandidates).toHaveBeenCalledTimes(2);
  });

  it("does not enable final approval while the server reports unresolved review work", async () => {
    mockReviewData([candidate()], { ...approveablePreview, can_approve: false, pending_candidate_ids: ["candidate-1"] });
    renderReview();

    expect(await screen.findByRole("button", { name: "Übernahme vorbereiten" })).toBeDisabled();
    expect(screen.getByText(/noch 1 offene/i)).toBeVisible();
  });

  it("opens an accessible modal and returns focus while discarding its key on close", async () => {
    mockReviewData();
    const randomUUID = vi.fn().mockReturnValueOnce("idempotency-1").mockReturnValueOnce("idempotency-2");
    vi.stubGlobal("crypto", { randomUUID });
    renderReview();

    const trigger = await screen.findByRole("button", { name: "Übernahme vorbereiten" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "Übernahme bestätigen" })).toHaveAttribute("aria-modal", "true");
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));

    await waitFor(() => expect(trigger).toHaveFocus());
    fireEvent.click(trigger);
    expect(randomUUID).toHaveBeenCalledTimes(2);
  });

  it("reuses one idempotency key after a network failure and navigates to the canonical property", async () => {
    const accepted = candidate({ id: "accepted", review_status: "ACCEPTED", operation: "CREATE" });
    const corrected = candidate({ id: "corrected", review_status: "CORRECTED", operation: "UPDATE" });
    const rejected = candidate({ id: "rejected", review_status: "REJECTED", operation: "CREATE" });
    mockReviewData([accepted, corrected, rejected]);
    const approve = vi.spyOn(declarationApi, "approveDeclarationImport")
      .mockRejectedValueOnce(new Error("Netzwerk nicht erreichbar"))
      .mockResolvedValueOnce({
      import_id: "import-1", status: "APPLIED", property_id: "property-canonical", draft_revision: 4,
      summary: { properties_created: 1, units_created: 2, units_updated: 0, rules_created: 1, documents_bound: 2 },
    });
    const randomUUID = vi.fn().mockReturnValueOnce("idempotency-1").mockReturnValueOnce("idempotency-2");
    vi.stubGlobal("crypto", { randomUUID });
    renderReview();

    fireEvent.click(await screen.findByRole("button", { name: "Übernahme vorbereiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Übernahme vorbereiten" }));
    expect(randomUUID).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "Übernahme bestätigen" })).toHaveTextContent(/Neue Einträge\s*1/);
    expect(screen.getByRole("dialog", { name: "Übernahme bestätigen" })).toHaveTextContent(/Aktualisierungen\s*1/);
    expect(screen.getByRole("dialog", { name: "Übernahme bestätigen" })).toHaveTextContent(/Abgelehnte Vorschläge\s*1/);
    fireEvent.click(screen.getByRole("button", { name: "Endgültig übernehmen" }));

    expect(await screen.findByText("Netzwerk nicht erreichbar")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Endgültig übernehmen" }));

    await waitFor(() => expect(approve).toHaveBeenCalledTimes(2));
    expect(approve).toHaveBeenNthCalledWith(1, "import-1", {
      draft_revision: 3, idempotency_key: "idempotency-1",
    });
    expect(approve).toHaveBeenNthCalledWith(2, "import-1", {
      draft_revision: 3, idempotency_key: "idempotency-1",
    });
    expect(await screen.findByText("Objekt property-canonical wurde übernommen.")).toBeVisible();
  });

  it("isolates dialog, approval key, and review data when the route changes to another import", async () => {
    const nextCandidates = deferred<DeclarationCandidate[]>();
    const nextDocuments = deferred<DeclarationDocument[]>();
    const nextPreview = deferred<ApprovalPreview>();
    vi.spyOn(declarationApi, "listDeclarationCandidates").mockImplementation((id) => id === "import-1" ? Promise.resolve([candidate()]) : nextCandidates.promise);
    vi.spyOn(declarationApi, "listDeclarationDocuments").mockImplementation((id) => id === "import-1" ? Promise.resolve(documents) : nextDocuments.promise);
    vi.spyOn(declarationApi, "getDeclarationApprovalPreview").mockImplementation((id) => id === "import-1" ? Promise.resolve(approveablePreview) : nextPreview.promise);
    const approve = vi.spyOn(declarationApi, "approveDeclarationImport");
    const randomUUID = vi.fn().mockReturnValueOnce("import-a-key").mockReturnValueOnce("import-b-key");
    vi.stubGlobal("crypto", { randomUUID });
    renderReview(true);

    fireEvent.click(await screen.findByRole("button", { name: "Übernahme vorbereiten" }));
    expect(screen.getByRole("dialog", { name: "Übernahme bestätigen" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Zu Import B wechseln" }));

    expect(screen.queryByRole("dialog", { name: "Übernahme bestätigen" })).not.toBeInTheDocument();
    expect(screen.getByText("Review-Daten werden geladen.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Endgültig übernehmen" })).not.toBeInTheDocument();
    expect(approve).not.toHaveBeenCalled();

    nextCandidates.resolve([candidate({ id: "candidate-b", proposed_value_json: { value: "Talstraße 8" } })]);
    nextDocuments.resolve(documents);
    nextPreview.resolve({ ...approveablePreview, import_id: "import-2", draft_revision: 8 });

    expect(await screen.findByText("Entwurfsstand 8")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Übernahme vorbereiten" }));
    expect(randomUUID).toHaveBeenCalledTimes(2);
  });
});
