import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { AccessDispositionPanel } from "./AccessDispositionPanel";

const items = [
  { id: "invoice_document:one", kind: "invoice_document", resource_id: "one", findings: ["UNKNOWN_CUSTODY"], disposition: "RETAIN_BLOCKED" },
  { id: "member:two", kind: "member", resource_id: "two", findings: ["EXPLICIT_TEAM_ASSIGNMENT_REVIEW"], disposition: "TEAM_REVIEW" },
];
const state = { manifest_digest: "a".repeat(64), assessment_digest: "b".repeat(64), current: true, reviewed: true, acceptance_available: true,
  ready_count: 0, retained_count: 1, team_count: 1, blocker_count: 0, active_work_count: 0, acceptance: null };
const receipt = { id: "accepted", accepted_at: "2026-10-02T21:00:00Z", reason: "Keep restricted legacy source", retained_count: 1, team_count: 1 };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const button = () => screen.getByRole("button", { name: "Altbestandsentscheidung speichern" });
async function fill() {
  await screen.findByText(/Rechnungsoriginal · Nach Umstellung/);
  fireEvent.change(screen.getByLabelText("Begründung der Altbestandsentscheidung"), { target: { value: receipt.reason } });
  for (const checkbox of screen.getAllByRole("checkbox")) fireEvent.click(checkbox);
}
function getMock(status = state) {
  return vi.spyOn(api, "get").mockImplementation(async url => url.includes("/items") ? { items, next_cursor: null } : status);
}

it("requires both explicit acknowledgments and every page", async () => {
  const get = vi.spyOn(api, "get").mockImplementation(async url => url.includes("/items") ? { items: [items[0]], next_cursor: "next" } : state);
  const post = vi.spyOn(api, "post");
  render(<AccessDispositionPanel snapshotId="snapshot" />);
  await fill();
  expect(button()).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
  get.mockImplementation(async url => url.includes("/items") ? { items: [items[1]], next_cursor: null } : state);
  fireEvent.click(screen.getByRole("button", { name: "Weitere laden" }));
  await waitFor(() => expect(button()).toBeEnabled());
  fireEvent.click(screen.getAllByRole("checkbox")[0]);
  expect(button()).toBeDisabled();
});

it("retains the exact decision after response loss and returns its saved receipt", async () => {
  const get = getMock();
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("lost response")).mockResolvedValueOnce(receipt);
  render(<AccessDispositionPanel snapshotId="snapshot" />);
  await fill(); fireEvent.click(button());
  await screen.findByText(/Entscheidung nicht bestätigt/);
  expect(screen.getByLabelText("Begründung der Altbestandsentscheidung")).toHaveValue(receipt.reason);
  get.mockResolvedValue({ ...state, acceptance: receipt, acceptance_available: false });
  fireEvent.click(button());
  await screen.findByText(/Entscheidung gespeichert/);
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(screen.queryByRole("button", { name: /aktivieren/i })).not.toBeInTheDocument();
});

it("requires fresh acknowledgment after stale evidence", async () => {
  getMock();
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "stale"));
  render(<AccessDispositionPanel snapshotId="snapshot" />);
  await fill(); fireEvent.click(button());
  await screen.findByText(/Die Grundlage ist veraltet/);
  expect(button()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Entscheidungsgrundlage erneut laden" }));
  await waitFor(() => expect(screen.getAllByRole("checkbox")[0]).toBeEnabled());
  expect(screen.getAllByRole("checkbox")[0]).not.toBeChecked();
  expect(screen.getByLabelText("Begründung der Altbestandsentscheidung")).toHaveValue(receipt.reason);
});

it("blocks unresolved resources and work even after acknowledgment", async () => {
  getMock({ ...state, blocker_count: 1, active_work_count: 1, acceptance_available: false });
  render(<AccessDispositionPanel snapshotId="snapshot" />);
  await fill();
  expect(button()).toBeDisabled();
  expect(screen.getByText(/Offene Einträge und laufende Vorgänge müssen/)).toBeInTheDocument();
});

it("never treats receipt replay as a verified current assessment", async () => {
  const get = getMock();
  vi.spyOn(api, "post").mockResolvedValue(receipt);
  render(<AccessDispositionPanel snapshotId="snapshot" />);
  await fill();
  get.mockRejectedValue(new Error("status unavailable"));
  fireEvent.click(button());
  await screen.findByText(/Der aktuelle Stand ist nicht bestätigt/);
  expect(screen.queryByRole("button", { name: "Altbestandsentscheidung speichern" })).not.toBeInTheDocument();
});
