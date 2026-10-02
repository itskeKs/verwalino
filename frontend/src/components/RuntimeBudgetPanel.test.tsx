import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { RuntimeBudgetPanel } from "./RuntimeBudgetPanel";

const policy = { enabled: false, max_cost_usd: "0.000001", window_minutes: 60, reservation_seconds: 120 };
const state = { policy, policy_digest: "a".repeat(64), review: null, matches_reviewed_policy: false };
const receipt = { id: "review-1", reviewed_at: "2026-10-02T16:00:00Z", reason: "Reviewed limit", policy, policy_digest: state.policy_digest };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function confirm() {
  await screen.findByText("0.000001 USD");
  fireEvent.change(screen.getByLabelText("Begründung der Budgetprüfung"), { target: { value: "Reviewed limit" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Budgetregel bestätigen" }));
}

it("shows exact configuration and requires explicit confirmation without automatic writes", async () => {
  vi.spyOn(api, "get").mockResolvedValue(state);
  const post = vi.spyOn(api, "post").mockResolvedValue(receipt);
  render(<RuntimeBudgetPanel />);
  await screen.findByText("0.000001 USD");
  expect(screen.getByText("Aus")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Budgetregel bestätigen" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("retains input and reuses the exact request after a lost response", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(state).mockResolvedValue({ ...state, review: receipt, matches_reviewed_policy: true });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("response lost")).mockResolvedValueOnce(receipt);
  render(<RuntimeBudgetPanel />);
  await confirm();
  await screen.findByText(/Prüfvermerk nicht bestätigt/);
  expect(screen.getByLabelText("Begründung der Budgetprüfung")).toHaveValue("Reviewed limit");
  fireEvent.click(screen.getByRole("button", { name: "Budgetregel bestätigen" }));
  await screen.findByText("Die aktuelle Budgetregel ist geprüft.");
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
});

it("requires a fresh read and acknowledgment after a stale review", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(state).mockResolvedValue({ ...state, policy_digest: "b".repeat(64) });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new ApiError(409, "stale")).mockResolvedValueOnce(receipt);
  render(<RuntimeBudgetPanel />);
  await confirm();
  await screen.findByText(/hat sich geändert/);
  expect(screen.getByRole("button", { name: "Budgetregel bestätigen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Budgetregel erneut laden" }));
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByLabelText("Begründung der Budgetprüfung")).toHaveValue("Reviewed limit");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Budgetregel bestätigen" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1]).toMatchObject({ policy_digest: "b".repeat(64) });
  expect(post.mock.calls[0][2]).not.toEqual(post.mock.calls[1][2]);
});

it("does not present a replayed receipt as the current policy approval", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(state).mockResolvedValue({ ...state, policy_digest: "b".repeat(64), review: receipt });
  vi.spyOn(api, "post").mockResolvedValue(receipt);
  render(<RuntimeBudgetPanel />);
  await confirm();
  await screen.findByText(/Prüfvermerk gespeichert/);
  expect(screen.queryByText("Die aktuelle Budgetregel ist geprüft.")).not.toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it("distinguishes a saved review from an unavailable current state", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(state).mockRejectedValueOnce(new Error("offline")).mockResolvedValue(state);
  vi.spyOn(api, "post").mockResolvedValue(receipt);
  render(<RuntimeBudgetPanel />);
  await confirm();
  await screen.findByText(/Der aktuelle Stand konnte nicht bestätigt werden/);
  expect(screen.getByRole("button", { name: "Budgetregel bestätigen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Budgetregel erneut laden" }));
  await waitFor(() => expect(screen.getByRole("checkbox")).toBeEnabled());
  expect(screen.getByLabelText("Begründung der Budgetprüfung")).toHaveValue("Reviewed limit");
});
