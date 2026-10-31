import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { PasswordResetRequestPage } from "./PasswordResetRequestPage";

const message = "Wenn ein Konto zu dieser Adresse besteht, erhalten Sie eine E-Mail zum Zurücksetzen Ihres Passworts.";
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ message }), { status: 202 })));
});
it("submits an email and the honeypot and replaces the form with a generic receipt", async () => {
  render(<MemoryRouter><PasswordResetRequestPage /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("E-Mail"), { target: { value: "erika@example.test" } });
  await userEvent.click(screen.getByRole("button", { name: "Link anfordern" }));
  expect(await screen.findByRole("status")).toHaveTextContent(message);
  expect(fetch).toHaveBeenCalledWith("/api/auth/password-reset", expect.objectContaining({ method: "POST", body: JSON.stringify({ email: "erika@example.test", website: "" }) }));
  expect(screen.queryByLabelText("E-Mail")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toHaveAttribute("href", "/login");
});
it("preserves the address and reports a failed request", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  render(<MemoryRouter><PasswordResetRequestPage /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("E-Mail"), { target: { value: "erika@example.test" } });
  await userEvent.click(screen.getByRole("button", { name: "Link anfordern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("nicht verfügbar");
  expect(screen.getByLabelText("E-Mail")).toHaveValue("erika@example.test");
});
