import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { EconomicPlan, HausgeldBookingRow } from "../api/types";
import { HausgeldBooking } from "./HausgeldBooking";

const plan = { id: "plan-1", property_id: "prop-1", plan_year: 2026, status: "finalized", superseded_by_id: null } as EconomicPlan;
const NAMES: Record<string, string> = { anna: "Anna Berg", karl: "Karl Weiß", lena: "Lena Roth" };
const ownerLabel = (id: string) => NAMES[id] ?? id;

function months(owner: string, from: number, to: number, amount: string, booked = false): HausgeldBookingRow[] {
  return Array.from({ length: to - from + 1 }, (_, index) => ({
    owner_profile_id: owner, period_year: 2026, period_month: from + index, amount,
    value_date: `2026-${String(from + index).padStart(2, "0")}-01`, booked,
  }));
}

const offered = [...months("anna", 1, 12, "600.00"), ...months("karl", 1, 6, "400.00"), ...months("lena", 7, 12, "400.00")];
const accounts = [
  { id: "bank-1", property_id: "prop-1", account_type: "LAUFEND", label: "Hausgeldkonto", iban: "DE02120300000000202051", is_active: true },
  { id: "bank-old", property_id: "prop-1", account_type: "LAUFEND", label: "Altes Konto", iban: null, is_active: false },
];

function mocks(rows: HausgeldBookingRow[] = offered) {
  return vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans/plan-1/hausgeld-booking") return rows as never;
    if (path === "/api/bank-accounts?property_id=prop-1") return accounts as never;
    throw new Error(`unexpected GET ${path}`);
  });
}

function show() {
  return render(<MemoryRouter><HausgeldBooking plan={plan} ownerLabel={ownerLabel} /></MemoryRouter>);
}

async function openFor(owner: string) {
  fireEvent.click(screen.getByRole("button", { name: "Hausgeld buchen" }));
  const select = await screen.findByLabelText("Eigentümer");
  fireEvent.change(select, { target: { value: owner } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("stays closed and loads nothing until the action is opened", () => {
  const get = mocks(); show();
  expect(screen.getByRole("button", { name: "Hausgeld buchen" })).toHaveAttribute("aria-expanded", "false");
  expect(get).not.toHaveBeenCalled();
});

it("offers the active accounts of the plan's property and the owners with their open months", async () => {
  mocks(); show();
  fireEvent.click(screen.getByRole("button", { name: "Hausgeld buchen" }));
  const account = await screen.findByLabelText("Bankkonto");
  expect(within(account).getAllByRole("option").map(o => o.textContent)).toEqual([
    "-- Bankkonto wählen --", "Hausgeldkonto (DE02 1203 0000 0000 2020 51)"]);
  const owners = within(screen.getByLabelText("Eigentümer")).getAllByRole("option").map(o => o.textContent);
  expect(owners).toEqual(["-- Eigentümer wählen --", "Anna Berg – 12 offen", "Karl Weiß – 6 offen", "Lena Roth – 6 offen"]);
});

it("shows twelve months with the plan amount and the due day as value date, all ticked", async () => {
  mocks(); show(); await openFor("anna");
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows).toHaveLength(12);
  expect(within(rows[0]).getByText("Januar 2026")).toBeVisible();
  expect(within(rows[11]).getByText("Dezember 2026")).toBeVisible();
  for (const row of rows) {
    expect(within(row).getByRole("checkbox")).toBeChecked();
    expect(within(row).getByLabelText(/Betrag/)).toHaveValue("600,00");
  }
  expect(within(rows[0]).getByLabelText(/Wertstellung/)).toHaveValue("2026-01-01");
  expect(within(rows[1]).getByLabelText(/Wertstellung/)).toHaveValue("2026-02-01");
});

it("shows a booked month as booked and never submits it", async () => {
  mocks([...months("anna", 1, 2, "600.00", true), ...months("anna", 3, 12, "600.00")]);
  const post = vi.spyOn(api, "post").mockResolvedValue([]); show(); await openFor("anna");
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("gebucht")).toBeVisible();
  expect(within(rows[0]).getByRole("checkbox")).toBeDisabled();
  expect(within(rows[0]).getByRole("checkbox")).not.toBeChecked();
  expect(within(rows[0]).queryByLabelText(/Betrag/)).not.toBeInTheDocument(); // the plan's values are not the booked ones
  expect(within(rows[2]).getByLabelText(/Betrag/)).toBeVisible();
  expect(screen.getByLabelText("Eigentümer")).toHaveDisplayValue("Anna Berg – 10 offen");
  expect(post).not.toHaveBeenCalled();
});

it("books the ticked months in one confirmed request with the edited amount and value date", async () => {
  mocks();
  const created = months("anna", 1, 12, "600.00").map((row, index) => ({ ...row, id: `p${index}`, status: "booked" }));
  const post = vi.spyOn(api, "post").mockResolvedValue(created as never);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  show(); await openFor("anna");
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  fireEvent.change(within(screen.getAllByRole("row")[1]).getByLabelText(/Wertstellung/), { target: { value: "2026-01-05" } });
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  expect(await screen.findByRole("status")).toHaveTextContent("12 Zahlungen über 7.200,00 € gebucht.");
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("12 Zahlungen über zusammen 7.200,00 € für Anna Berg buchen?"));
  expect(post).toHaveBeenCalledTimes(1);
  const [path, body] = post.mock.calls[0] as [string, { bank_account_id: string; rows: Record<string, unknown>[] }];
  expect(path).toBe("/api/economic-plans/plan-1/hausgeld-booking");
  expect(body.bank_account_id).toBe("bank-1");
  expect(body.rows).toHaveLength(12);
  expect(body.rows[0]).toEqual({ owner_profile_id: "anna", period_year: 2026, period_month: 1, amount: "600.00", value_date: "2026-01-05" });
  expect(body.rows[11]).toEqual({ owner_profile_id: "anna", period_year: 2026, period_month: 12, amount: "600.00", value_date: "2026-12-01" });
  expect(screen.getByRole("link", { name: "Zahlungen ansehen" })).toHaveAttribute("href", "/zahlungen?bank_account_id=bank-1&payment_type=HAUSGELD_VORSCHUSS");
});

it("sends only the ticked months and accepts a decimal comma amount", async () => {
  mocks();
  const post = vi.spyOn(api, "post").mockResolvedValue([] as never);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  show(); await openFor("karl");
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  const rows = screen.getAllByRole("row").slice(1);
  for (const row of rows.slice(2)) fireEvent.click(within(row).getByRole("checkbox"));
  fireEvent.change(within(rows[1]).getByLabelText(/Betrag/), { target: { value: "390,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  const body = post.mock.calls[0][1] as { rows: { period_month: number; amount: string }[] };
  expect(body.rows.map(r => [r.period_month, r.amount])).toEqual([[1, "400.00"], [2, "390.50"]]);
});

it("does not book when the confirmation is declined", async () => {
  mocks(); const post = vi.spyOn(api, "post").mockResolvedValue([] as never);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  show(); await openFor("anna");
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  expect(post).not.toHaveBeenCalled();
});

it("blocks booking without an account, without a ticked month and with an invalid amount", async () => {
  mocks(); const post = vi.spyOn(api, "post").mockResolvedValue([] as never);
  show(); await openFor("anna");
  const book = screen.getByRole("button", { name: "Ausgewählte Monate buchen" });
  expect(book).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  expect(book).toBeEnabled();
  const first = within(screen.getAllByRole("row")[1]).getByLabelText(/Betrag/);
  for (const bad of ["0,00", "abc", "", "12,345"]) {
    fireEvent.change(first, { target: { value: bad } });
    expect(book).toBeDisabled();
    expect(first).toHaveAttribute("aria-invalid", "true");
  }
  expect(screen.getByText("Bitte geben Sie für jeden ausgewählten Monat einen Betrag über 0,00 € und ein Wertstellungsdatum an.")).toBeVisible();
  fireEvent.change(first, { target: { value: "600" } });
  expect(book).toBeEnabled();
  for (const row of screen.getAllByRole("row").slice(1)) fireEvent.click(within(row).getByRole("checkbox"));
  expect(book).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("reports a refusal and keeps the entries so the booking can be repeated", async () => {
  mocks();
  const post = vi.spyOn(api, "post")
    .mockRejectedValueOnce(new ApiError(409, "Für Anna Berg ist der Monat März 2026 bereits gebucht."))
    .mockResolvedValueOnce([] as never);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  show(); await openFor("anna");
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  fireEvent.change(within(screen.getAllByRole("row")[2]).getByLabelText(/Betrag/), { target: { value: "590,00" } });
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Für Anna Berg ist der Monat März 2026 bereits gebucht.");
  expect(within(screen.getAllByRole("row")[2]).getByLabelText(/Betrag/)).toHaveValue("590,00");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
});

it("reloads the offer after booking so the months show as booked", async () => {
  const get = mocks();
  const post = vi.spyOn(api, "post").mockResolvedValue(months("anna", 1, 12, "600.00") as never);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  show(); await openFor("anna");
  fireEvent.change(screen.getByLabelText("Bankkonto"), { target: { value: "bank-1" } });
  get.mockImplementation(async path => {
    if (path === "/api/economic-plans/plan-1/hausgeld-booking") return months("anna", 1, 12, "600.00", true) as never;
    return accounts as never;
  });
  fireEvent.click(screen.getByRole("button", { name: "Ausgewählte Monate buchen" }));
  await screen.findByRole("status");
  expect(post).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(screen.getAllByText("gebucht")).toHaveLength(12));
  expect(screen.getByRole("button", { name: "Ausgewählte Monate buchen" })).toBeDisabled();
});

it("says so when the plan has nothing to book and when loading fails", async () => {
  mocks([]); const view = show();
  fireEvent.click(screen.getByRole("button", { name: "Hausgeld buchen" }));
  expect(await screen.findByText("Der Plan enthält kein Hausgeld, das gebucht werden kann.")).toBeVisible();
  view.unmount();
  vi.spyOn(api, "get").mockRejectedValue(new ApiError(422, "Die Eigentumszeiträume oder Miteigentumsanteile für das Hausgeld sind unvollständig."));
  show();
  fireEvent.click(screen.getByRole("button", { name: "Hausgeld buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Die Eigentumszeiträume oder Miteigentumsanteile für das Hausgeld sind unvollständig.");
});

it("keeps twelve rows with long owner names usable", async () => {
  const long = "Anna-Katharina Freifrau von Hohenzollern-Sigmaringen";
  vi.spyOn(api, "get").mockImplementation(async path => (path.startsWith("/api/bank-accounts") ? accounts : months("anna", 1, 12, "1234.56")) as never);
  render(<MemoryRouter><HausgeldBooking plan={plan} ownerLabel={() => long} /></MemoryRouter>);
  await openFor("anna");
  expect(screen.getByLabelText("Eigentümer")).toHaveDisplayValue(`${long} – 12 offen`);
  expect(within(screen.getAllByRole("row")[1]).getByLabelText(/Betrag/)).toHaveValue("1234,56");
});
