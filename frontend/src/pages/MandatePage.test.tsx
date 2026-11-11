import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AuthCtx } from "../auth/useAuth";
import { MandatePage } from "./MandatePage";

const mandate = { id: "mandate-1", mandate_type: "WEG", label: "WEG Beispiel", start_date: "2026-01-01", end_date: null };

function renderPage(role: string) {
  return render(
    <AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() } as never}>
      <MemoryRouter>
        <MandatePage />
      </MemoryRouter>
    </AuthCtx.Provider>,
  );
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("lets an owner admin create a mandate and shows it in the reloaded list", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce([] as never).mockResolvedValue([mandate] as never);
  const post = vi.spyOn(api, "post").mockResolvedValue(mandate as never);
  renderPage("OWNER_ADMIN");
  await screen.findByText(/Noch keine Mandate angelegt/);

  fireEvent.click(screen.getByRole("button", { name: /Neues Mandat/ }));
  fireEvent.change(screen.getByLabelText("Bezeichnung"), { target: { value: "WEG Beispiel" } });
  fireEvent.change(screen.getByLabelText("Beginn *"), { target: { value: "2026-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Mandat anlegen" }));

  expect(await screen.findByRole("link", { name: "Öffnen" })).toBeVisible();
  expect(screen.getByText("WEG Beispiel")).toBeVisible();
  expect(within(screen.getByRole("table")).getByText("WEG-Verwaltung")).toBeVisible();
  expect(screen.getByText("laufend")).toBeVisible();
  expect(post).toHaveBeenCalledWith("/api/mandates", {
    mandate_type: "WEG", label: "WEG Beispiel", start_date: "2026-01-01", end_date: null,
  });
});

it("shows the server error in the form and offers no create button to a clerk", async () => {
  vi.spyOn(api, "get").mockResolvedValue([] as never);
  const post = vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "Es besteht bereits ein aktives Mandat dieser Art."));
  renderPage("OWNER_ADMIN");
  fireEvent.click(await screen.findByRole("button", { name: /Neues Mandat/ }));
  fireEvent.click(screen.getByRole("button", { name: "Mandat anlegen" }));
  expect(await screen.findByText("Es besteht bereits ein aktives Mandat dieser Art.")).toBeVisible();
  expect(post).toHaveBeenCalledWith("/api/mandates", expect.objectContaining({ mandate_type: "WEG", end_date: null }));

  cleanup();
  renderPage("CLERK");
  await screen.findByText(/Noch keine Mandate angelegt/);
  expect(screen.queryByRole("button", { name: /Neues Mandat/ })).not.toBeInTheDocument();
});
