import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScopeControl } from "./ScopeControl";
import { api } from "../api/client";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const initial = { mode: "compatibility", event: null };
const event = { id: "pause", mode: "paused", reason: "Review", created_at: "2026-10-02T10:00:00Z" };
const pause = () => screen.getByRole("button", { name: "Bereichsbezogene Finanzaktionen pausieren" });

it("reuses a lost command but refreshes current state instead of treating its old receipt as current", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(initial).mockResolvedValueOnce({ mode: "compatibility", event: { ...event, id: "resumed", mode: "compatibility", reason: "Later resume" } });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost")).mockResolvedValue(event);
  render(<ScopeControl />);
  await screen.findByText("Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv");
  expect(pause()).toBeDisabled();
  expect(screen.getByText(/keine vollständige Buchungssperre/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Begründung der Betriebsänderung"), { target: { value: "Review" } });
  fireEvent.click(pause());
  await screen.findByRole("alert");
  fireEvent.click(pause());
  await screen.findByText(/Later resume/);
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(post.mock.calls[0][1]).toEqual({ mode: "paused", expected_event_id: null, reason: "Review" });
  expect(screen.queryByText("Bereichsbezogene Finanzaktionen pausiert")).toBeNull();
});

it("treats a failed status load as unavailable and recovers using the observed event predecessor", async () => {
  vi.spyOn(api, "get").mockRejectedValueOnce(new Error("Denied")).mockResolvedValue({ mode: "paused", event });
  const post = vi.spyOn(api, "post").mockResolvedValue({ ...event, mode: "compatibility" });
  render(<ScopeControl />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Status nicht verfügbar");
  expect(screen.queryByLabelText("Begründung der Betriebsänderung")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Betriebsstatus neu laden" }));
  await screen.findByText("Bereichsbezogene Finanzaktionen pausiert");
  fireEvent.change(screen.getByLabelText("Begründung der Betriebsänderung"), { target: { value: "Resume reviewed" } });
  fireEvent.click(screen.getByRole("button", { name: "Bereichsbezogene Finanzaktionen wieder aufnehmen" }));
  await waitFor(() => expect(post).toHaveBeenCalled());
  expect(post.mock.calls[0][1]).toEqual({ mode: "compatibility", expected_event_id: "pause", reason: "Resume reviewed" });
});

it("does not show the old state when refreshing after a successful command fails", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(initial).mockRejectedValueOnce(new Error("Refresh failed"));
  vi.spyOn(api, "post").mockResolvedValue(event);
  render(<ScopeControl />);
  await screen.findByText("Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv");
  fireEvent.change(screen.getByLabelText("Begründung der Betriebsänderung"), { target: { value: "Review" } });
  fireEvent.click(pause());
  await screen.findByRole("alert");
  expect(screen.queryByText("Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv")).toBeNull();
  expect(screen.queryByLabelText("Begründung der Betriebsänderung")).toBeNull();
});
