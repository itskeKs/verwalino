import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MeterPanel } from "./MeterPanel";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Meter, MeterReading } from "../api/types";

vi.mock("../api/endpoints", () => ({ listMeters: vi.fn(), createMeter: vi.fn(), updateMeter: vi.fn(), listMeterReadings: vi.fn(), createMeterReading: vi.fn() }));
const meter: Meter = { id: "meter", organization_id: "org", property_id: "property", unit_id: null, meter_number: "W-1", meter_type: "KALTWASSER", location: "Keller" };
const readings: MeterReading[] = [
  { id: "r1", meter_id: "meter", reading_date: "2026-01-01", value: "100.0000", period: "2026" },
  { id: "r2", meter_id: "meter", reading_date: "2026-12-31", value: "250.1250", period: "2026" },
];
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(endpoints.listMeters).mockResolvedValue([{ ...meter }]);
  vi.mocked(endpoints.listMeterReadings).mockResolvedValue(readings.map(row => ({ ...row })));
  vi.mocked(endpoints.createMeter).mockResolvedValue({ ...meter, id: "new-meter", unit_id: "unit", meter_number: "NEW" });
  vi.mocked(endpoints.createMeterReading).mockResolvedValue({ ...readings[1], id: "new-reading" });
  vi.mocked(endpoints.updateMeter).mockResolvedValue({ ...meter, location: "Bad" });
});
function show(canWrite = true, unitId?: string) {
  return render(<MeterPanel propertyId="property" unitId={unitId} canWrite={canWrite} />);
}
async function history() {
  await screen.findByText("W-1");
  fireEvent.click(screen.getByRole("button", { name: "Verlauf anzeigen" }));
  await screen.findByRole("button", { name: "Stand erfassen" });
}
async function readingForm() {
  await history();
  fireEvent.click(screen.getByRole("button", { name: "Stand erfassen" }));
  fireEvent.change(screen.getByLabelText("Ablesedatum *", { exact: true }), { target: { value: "2026-12-31" } });
}
it("loads exact property/unit context and exposes the meter region", async () => {
  show(true, "unit");
  expect(await screen.findByRole("region", { name: "Zähler" })).toBeVisible();
  await screen.findByText("W-1");
  expect(endpoints.listMeters).toHaveBeenCalledWith("property", "unit");
});
it("shows the fixed four-place consumption without float rounding", async () => {
  show(); await history();
  expect(await screen.findByText(/150,1250/)).toBeVisible();
  expect(screen.getByText(/Verbrauch/)).toBeVisible();
});
it("keeps read-only history visible without any mutation action", async () => {
  show(false); await screen.findByText("W-1");
  fireEvent.click(screen.getByRole("button", { name: "Verlauf anzeigen" }));
  expect(await screen.findByText(/150,1250/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Zähler anlegen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Stand erfassen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Zähler bearbeiten" })).not.toBeInTheDocument();
});
it("creates only within the fixed unit using full known payload fields", async () => {
  show(true, "unit"); await screen.findByText("W-1");
  fireEvent.click(screen.getByRole("button", { name: "Zähler anlegen" }));
  for (const [label, value] of [["Zählernummer *", "NEW"], ["Zählertyp *", "KALTWASSER"], ["Standort", "Bad"]]) {
    fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Zähler speichern" }));
  await waitFor(() => expect(endpoints.createMeter).toHaveBeenCalledWith({ property_id: "property", unit_id: "unit", meter_number: "NEW", meter_type: "KALTWASSER", location: "Bad" }));
});
it("posts a German reading as an exact decimal string", async () => {
  show(); await readingForm();
  fireEvent.change(screen.getByLabelText("Zählerstand *", { exact: true }), { target: { value: "250,1250" } });
  fireEvent.change(screen.getByLabelText("Zeitraum", { exact: true }), { target: { value: "2026" } });
  fireEvent.click(screen.getByRole("button", { name: "Stand speichern" }));
  await waitFor(() => expect(endpoints.createMeterReading).toHaveBeenCalledWith("meter", { reading_date: "2026-12-31", value: "250.1250", period: "2026" }));
});
it.each(["", "-1", "NaN", "Infinity", "1.00001", "10000000000", "1e3"])("rejects invalid reading %s without posting", async value => {
  show(); await readingForm();
  fireEvent.change(screen.getByLabelText("Zählerstand *", { exact: true }), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Stand speichern" }));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(endpoints.createMeterReading).not.toHaveBeenCalled();
});
it("retains a reading on real ApiError and allows retry", async () => {
  vi.mocked(endpoints.createMeterReading).mockRejectedValueOnce(new ApiError(422, "Stand prüfen"));
  show(); await readingForm();
  const input = screen.getByLabelText("Zählerstand *", { exact: true });
  fireEvent.change(input, { target: { value: "250,1250" } });
  fireEvent.click(screen.getByRole("button", { name: "Stand speichern" }));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(input).toHaveValue("250,1250");
  fireEvent.click(screen.getByRole("button", { name: "Stand speichern" }));
  await waitFor(() => expect(endpoints.createMeterReading).toHaveBeenCalledTimes(2));
});

it("accepts the exact Numeric(14,4) maximum", async () => {
  show(); await readingForm();
  fireEvent.change(screen.getByLabelText("Zählerstand *", { exact: true }), { target: { value: "9999999999,9999" } });
  fireEvent.click(screen.getByRole("button", { name: "Stand speichern" }));
  await waitFor(() => expect(endpoints.createMeterReading).toHaveBeenCalledWith("meter", {
    reading_date: "2026-12-31", value: "9999999999.9999", period: null,
  }));
});
it("subtracts large readings exactly at the fourth decimal place", async () => {
  vi.mocked(endpoints.listMeterReadings).mockResolvedValue([
    { ...readings[0], value: "9999999999.9998" }, { ...readings[1], value: "9999999999.9999" },
  ]);
  show(); await history();
  expect(await screen.findByRole("status", { name: "Verbrauch" })).toHaveTextContent("Verbrauch: 0,0001");
});
it.each(["decreasing", "duplicate-start"])("does not invent consumption for %s readings", async kind => {
  vi.mocked(endpoints.listMeterReadings).mockResolvedValue(kind === "decreasing"
    ? [{ ...readings[0], value: "300.0000" }, readings[1]]
    : [readings[0], { ...readings[0], id: "r-other", value: "101.0000" }, readings[1]]);
  show(); await history();
  expect(await screen.findByRole("status", { name: "Verbrauch" })).toHaveTextContent("Ablesungen prüfen");
  expect(screen.queryByText(/Verbrauch: 150,1250/)).not.toBeInTheDocument();
});
it("makes list errors visible and retries the same context", async () => {
  vi.mocked(endpoints.listMeters).mockRejectedValueOnce(new ApiError(403, "Kein Zugriff"));
  show(); expect(await screen.findByRole("alert")).toHaveTextContent("Kein Zugriff");
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  expect(await screen.findByText("W-1")).toBeVisible();
  expect(endpoints.listMeters).toHaveBeenLastCalledWith("property", undefined);
});
it("locks repeated submission and cancellation synchronously", async () => {
  let finish!: (row: MeterReading) => void;
  vi.mocked(endpoints.createMeterReading).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  show(); await readingForm();
  fireEvent.change(screen.getByLabelText("Zählerstand *", { exact: true }), { target: { value: "250,1250" } });
  const form = screen.getByRole("form", { name: "Stand erfassen" });
  fireEvent.submit(form); fireEvent.submit(form);
  expect(endpoints.createMeterReading).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  expect(screen.getByLabelText("Zählerstand *", { exact: true })).toBeDisabled();
  await act(async () => finish({ ...readings[1], id: "new" }));
  expect(screen.queryByRole("form", { name: "Stand erfassen" })).not.toBeInTheDocument();
});
it("ignores a late list after the property context changes", async () => {
  let finish!: (rows: Meter[]) => void;
  vi.mocked(endpoints.listMeters).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = show();
  vi.mocked(endpoints.listMeters).mockResolvedValue([{ ...meter, id: "other", property_id: "other-property", meter_number: "W-2" }]);
  view.rerender(<MeterPanel propertyId="other-property" canWrite={false} />);
  expect(await screen.findByText("W-2")).toBeVisible();
  await act(async () => finish([meter]));
  expect(screen.queryByText("W-1")).not.toBeInTheDocument();
  expect(endpoints.listMeters).toHaveBeenLastCalledWith("other-property", undefined);
});
it("ignores the previous meter's late history", async () => {
  let finish!: (rows: MeterReading[]) => void;
  vi.mocked(endpoints.listMeters).mockResolvedValue([meter, { ...meter, id: "other", meter_number: "W-2" }]);
  vi.mocked(endpoints.listMeterReadings).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue([{ ...readings[0], value: "333.0000" }, { ...readings[1], value: "334.0000" }]);
  show(); await screen.findByText("W-1");
  fireEvent.click(within(screen.getByRole("row", { name: /W-1/ })).getByRole("button", { name: "Verlauf anzeigen" }));
  fireEvent.click(within(screen.getByRole("row", { name: /W-2/ })).getByRole("button", { name: "Verlauf anzeigen" }));
  expect(await screen.findByRole("status", { name: "Verbrauch" })).toHaveTextContent("Verbrauch: 1,0000");
  await act(async () => finish(readings));
  expect(screen.getByRole("status", { name: "Verbrauch" })).toHaveTextContent("Verbrauch: 1,0000");
  expect(screen.queryByText(/150,1250/)).not.toBeInTheDocument();
});
it("patches descriptors without reparenting or changing loaded readings", async () => {
  show(); await history();
  fireEvent.click(screen.getByRole("button", { name: "Zähler bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Standort", { exact: true }), { target: { value: "Bad" } });
  fireEvent.click(screen.getByRole("button", { name: "Zähler speichern" }));
  await waitFor(() => expect(endpoints.updateMeter).toHaveBeenCalledWith("meter", { meter_number: "W-1", meter_type: "KALTWASSER", location: "Bad" }));
  expect(endpoints.createMeter).not.toHaveBeenCalled();
  expect(endpoints.createMeterReading).not.toHaveBeenCalled();
  expect(await screen.findByRole("status", { name: "Verbrauch" })).toHaveTextContent("Verbrauch: 150,1250");
  expect(endpoints.listMeterReadings).toHaveBeenCalledTimes(1);
});
