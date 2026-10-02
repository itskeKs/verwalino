import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { SourceSecurityPanel } from "./SourceSecurityPanel";

const decision = { id: "decision-1", snapshot_id: "snapshot-1", manifest_digest: "a".repeat(64), assessment_digest: "b".repeat(64) };
const evidence = { current: true, reviewed: true, blocker_count: 0, active_work_count: 0, acceptance: decision, ...decision };
const initial = { mode: "PREPARATION", changed_at: null, last_transition: null };
const receipt = { id: "transition-1", target_mode: "ENFORCED", changed_at: "2026-10-02T20:00:00Z", reason: "Explicit switch" };
const active = { mode: "ENFORCED", changed_at: receipt.changed_at, last_transition: receipt };
const paused = { mode: "PAUSED", changed_at: "2026-10-02T20:01:00Z", last_transition: { ...receipt, id: "pause-1", target_mode: "PAUSED" } };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function confirm(action = "Zugriffsregeln aktivieren") {
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Begründung der Umstellung"), { target: { value: "Explicit switch" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: action }));
}

it("requires an explicitly selected and verified decision and separate acknowledgment", async () => {
  vi.spyOn(api, "get").mockResolvedValue(initial);
  const post = vi.spyOn(api, "post");
  render(<SourceSecurityPanel decision={null} />);
  await screen.findByText("Vorbereitung");
  expect(screen.getByRole("button", { name: "Zugriffsregeln aktivieren" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("preserves reason and exact request after response loss, then refreshes current mode", async () => {
  let applied = false;
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/disposition") ? evidence : applied ? active : initial);
  const post = vi.spyOn(api, "post").mockImplementationOnce(async () => { throw new Error("lost"); }).mockImplementationOnce(async () => { applied = true; return receipt; });
  render(<SourceSecurityPanel decision={decision} />);
  await confirm();
  await screen.findByText(/Umstellung nicht bestätigt/);
  expect(screen.getByLabelText("Begründung der Umstellung")).toHaveValue("Explicit switch");
  fireEvent.click(screen.getByRole("button", { name: "Zugriffsregeln aktivieren" }));
  await screen.findByText("Zugriffsregeln aktiv");
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Aktionen pausieren" })).toBeDisabled();
});

it("allows pause without fetching selected snapshot or budget evidence", async () => {
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/disposition")) throw new Error("registrar unavailable");
    return active;
  });
  const post = vi.spyOn(api, "post").mockResolvedValue(paused.last_transition);
  render(<SourceSecurityPanel decision={decision} />);
  await confirm("Aktionen pausieren");
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(get.mock.calls.every(([path]) => !path.endsWith("/disposition"))).toBe(true);
  expect(post.mock.calls[0][1]).toMatchObject({ target_mode: "PAUSED", acceptance_id: null, manifest_digest: null, assessment_digest: null });
});

it("blocks stale selected evidence and requires reload and acknowledgment after conflict", async () => {
  let stale = false;
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/disposition") ? { ...evidence, current: !stale } : initial);
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "stale"));
  render(<SourceSecurityPanel decision={decision} />);
  await confirm();
  await screen.findByText(/Zustand oder Prüfgrundlage hat sich geändert/);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Zugriffsregeln aktivieren" })).toBeDisabled();
  stale = true;
  fireEvent.click(screen.getByRole("button", { name: "Zugriffsmodus erneut laden" }));
  await screen.findByText(/ausgewählte Grundlage ist nicht mehr aktuell/);
  expect(screen.getByRole("checkbox")).toBeDisabled();
});

it("separates historical activation receipts from a currently paused mode", async () => {
  let applied = false;
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/disposition") ? evidence : applied ? paused : initial);
  vi.spyOn(api, "post").mockImplementation(async () => { applied = true; return receipt; });
  render(<SourceSecurityPanel decision={decision} />);
  await confirm();
  await screen.findByText("Aktionen pausiert");
  expect(screen.getByText(/Entscheidung gespeichert: Zugriffsregeln aktiv/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Aktionen wieder aufnehmen" })).toBeDisabled();
});

it("keeps saved receipt separate when the status refresh fails", async () => {
  let applied = false;
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (applied) throw new Error("offline");
    return path.endsWith("/disposition") ? evidence : initial;
  });
  vi.spyOn(api, "post").mockImplementation(async () => { applied = true; return receipt; });
  render(<SourceSecurityPanel decision={decision} />);
  await confirm();
  await screen.findByText(/aktuelle Zugriffsmodus ist nicht bestätigt/);
  expect(screen.getByText(/Zuletzt gelesener Modus/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Zugriffsregeln aktivieren" })).toBeDisabled();
});

it("resets acknowledgment when a different accepted decision is selected", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/disposition") ? evidence : initial);
  const { rerender } = render(<SourceSecurityPanel decision={decision} />);
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  fireEvent.click(screen.getByRole("checkbox"));
  rerender(<SourceSecurityPanel decision={{ ...decision, id: "decision-2", snapshot_id: "snapshot-2" }} />);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  await screen.findByText(/ausgewählte Grundlage ist nicht mehr aktuell/);
});

it("clears acknowledgment before a same-ID selection reread can change the action", async () => {
  let reread = false;
  let resolveStatus: (value: typeof active) => void = () => {};
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/disposition")) return evidence;
    if (!reread) return initial;
    return new Promise<typeof active>(resolve => { resolveStatus = resolve; });
  });
  const { rerender } = render(<SourceSecurityPanel decision={decision} />);
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Begründung der Umstellung"), { target: { value: "Old activation reason" } });
  fireEvent.click(screen.getByRole("checkbox"));
  reread = true;
  rerender(<SourceSecurityPanel decision={{ ...decision }} />);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  resolveStatus(active);
  await screen.findByText("Zugriffsregeln aktiv");
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Aktionen pausieren" })).toBeDisabled();
});
