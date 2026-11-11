import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { VisualDisclosuresPage } from "./VisualDisclosuresPage";

vi.mock("../components/VisualDisclosureList", () => ({
  VisualDisclosureList: ({ endpoint }: { endpoint: string }) => <div data-testid="disclosure-list" data-endpoint={endpoint} />,
}));
vi.mock("../components/SearchChoice", () => ({
  SearchChoice: ({ label, onChange }: { label: string; onChange: (item: { id: string; label: string; scope_type: string } | null) => void }) => (
    <button type="button" aria-label={label} onClick={() => onChange({ id: "scope-1", label: "WEG Haus", scope_type: "WEG" })} />
  ),
}));

function renderPage() {
  return render(<MemoryRouter><VisualDisclosuresPage /></MemoryRouter>);
}

afterEach(() => { cleanup(); });

it("asks for a scope first and lists the disclosures of the chosen scope", async () => {
  renderPage();
  expect(screen.getByText("Wählen Sie einen Bereich mit Leseberechtigung.")).toBeVisible();
  expect(screen.queryByTestId("disclosure-list")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Zielbereich" }));
  expect(await screen.findByTestId("disclosure-list")).toHaveAttribute("data-endpoint", "/api/accounting-scopes/scope-1/visual-disclosures");
});

it("switches to the outgoing disclosures of the own user", async () => {
  renderPage();
  fireEvent.click(screen.getByRole("radio", { name: "Meine ausgehenden Freigaben" }));

  expect(await screen.findByTestId("disclosure-list")).toHaveAttribute("data-endpoint", "/api/visual-disclosures/outgoing");
  expect(screen.getByText(/Eigene Freigaben können Sie auch dann widerrufen/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Zielbereich" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("radio", { name: "Auszüge im Zielbereich" }));
  expect(await screen.findByText("Wählen Sie einen Bereich mit Leseberechtigung.")).toBeVisible();
});
