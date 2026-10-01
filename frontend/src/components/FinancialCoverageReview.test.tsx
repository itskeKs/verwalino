import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FinancialCoverageReview } from "./FinancialCoverageReview";

const json = (body: unknown, status = 201) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("requires reason and explicit completeness for zero, and rechecks after an edit", async () => {
  const fetchMock = vi.fn(async () => json({ id: "evidence-1" }));
  vi.stubGlobal("fetch", fetchMock);
  const props = { kind: "settlement" as const, subjectId: "run-1", values: { advance_payments_total: "0.00" } };
  const view = render(<FinancialCoverageReview {...props} />);
  const save = screen.getByRole("button", { name: "Datengrundlage speichern" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Complete bank statements checked" } });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(save);
  await screen.findByRole("status");
  const init = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0][1];
  expect(JSON.parse(init.body as string)).toMatchObject({ values: { advance_payments_total: "0.00" }, confirmed_complete: true });
  view.rerender(<FinancialCoverageReview {...props} values={{ advance_payments_total: "100.00" }} />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(save).toBeDisabled();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("keeps review text after stale derived input and explains recovery", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ detail: { code: "financial_inputs_incomplete", blockers: [{ code: "derived_total_stale" }] } }, 422)));
  render(<FinancialCoverageReview kind="settlement" subjectId="run-1" values={{ advance_payments_total: null }} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Retain review note" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("zuerst neu aus den Zahlungen ermitteln"));
  expect(screen.getByRole("textbox")).toHaveValue("Retain review note");
  expect(screen.queryByRole("status")).toBeNull();
});
