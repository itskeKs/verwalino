import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { getDunningSettings, saveDunningSettings } from "../api/endpoints";
import { DunningSettingsForm } from "./DunningSettingsForm";

vi.mock("../api/endpoints", () => ({ getDunningSettings: vi.fn(), saveDunningSettings: vi.fn() }));

const NOTICE = /Der amtliche Basiszinssatz ist nur bis 31\.12\.2026 hinterlegt\./;

function settings(covered: string | null) {
  return {
    fee_level_1: "0.00", fee_level_2: "2.50", fee_level_3: "2.50",
    default_interest_enabled: false, base_rate_covered_until: covered,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-11-08T12:00:00"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.mocked(getDunningSettings).mockReset();
  vi.mocked(saveDunningSettings).mockReset();
});

it("warns administrators once fewer than 60 days of base-rate coverage remain", async () => {
  vi.mocked(getDunningSettings).mockResolvedValue(settings("2026-12-31"));
  render(<DunningSettingsForm editable />);
  expect(await screen.findByText(NOTICE)).toBeInTheDocument();
});

it("starts the warning exactly 60 days before the covered-until date", async () => {
  vi.setSystemTime(new Date("2026-11-01T12:00:00"));
  vi.mocked(getDunningSettings).mockResolvedValue(settings("2026-12-31"));
  render(<DunningSettingsForm editable />);
  expect(await screen.findByText(NOTICE)).toBeInTheDocument();
});

it("stays silent while more than 60 days remain covered", async () => {
  vi.setSystemTime(new Date("2026-10-31T12:00:00"));
  vi.mocked(getDunningSettings).mockResolvedValue(settings("2026-12-31"));
  render(<DunningSettingsForm editable />);
  await screen.findByLabelText("Mahngebühr Stufe 1 (EUR)");
  expect(screen.queryByText(/Basiszinssatz/)).toBeNull();
});

it("keeps warning after the stored table has ended", async () => {
  vi.setSystemTime(new Date("2027-01-15T12:00:00"));
  vi.mocked(getDunningSettings).mockResolvedValue(settings("2026-12-31"));
  render(<DunningSettingsForm editable />);
  expect(await screen.findByText(NOTICE)).toBeInTheDocument();
});

it("renders no warning when the table reports no coverage end", async () => {
  vi.mocked(getDunningSettings).mockResolvedValue(settings(null));
  render(<DunningSettingsForm editable />);
  await screen.findByLabelText("Mahngebühr Stufe 1 (EUR)");
  expect(screen.queryByText(/Basiszinssatz/)).toBeNull();
});

it("saves the policy without the read-only coverage field and keeps the warning", async () => {
  vi.mocked(getDunningSettings).mockResolvedValue(settings("2026-12-31"));
  vi.mocked(saveDunningSettings).mockResolvedValue({
    fee_level_1: "0.00", fee_level_2: "2.50", fee_level_3: "2.50", default_interest_enabled: false,
  });
  render(<DunningSettingsForm editable />);
  fireEvent.submit((await screen.findByLabelText("Mahngebühr Stufe 1 (EUR)")).closest("form")!);
  await screen.findByText("Mahneinstellungen gespeichert.");
  expect(vi.mocked(saveDunningSettings)).toHaveBeenCalledWith({
    fee_level_1: "0.00", fee_level_2: "2.50", fee_level_3: "2.50", default_interest_enabled: false,
  });
  expect(screen.getByText(NOTICE)).toBeInTheDocument();
});
