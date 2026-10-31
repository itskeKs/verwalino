import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { OrganizationDeletionBanner } from "./OrganizationDeletionBanner";

vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "OWNER_ADMIN" } }) }));
afterEach(() => vi.resetAllMocks());

it("shows the real cooling-off deadline and cancels without claiming early success", async () => {
  vi.mocked(api.get).mockResolvedValue({ deletion: { status: "cooling_off", execute_after: "2026-11-07T12:00:00Z" } });
  vi.mocked(api.post).mockRejectedValueOnce(new Error("Widerruf fehlgeschlagen."));
  render(<OrganizationDeletionBanner />);
  fireEvent.click(await screen.findByRole("button", { name: "Löschung widerrufen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Widerruf fehlgeschlagen.");
  expect(screen.getByRole("status")).toHaveTextContent("schreibgeschützt");
  expect(screen.getByRole("status")).toHaveTextContent("07.11.2026");
  vi.mocked(api.post).mockResolvedValue({ status: "cancelled" });
  vi.mocked(api.get).mockResolvedValue({ deletion: { status: "cancelled", execute_after: null } });
  fireEvent.click(screen.getByRole("button", { name: "Löschung widerrufen" }));
  await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
});
