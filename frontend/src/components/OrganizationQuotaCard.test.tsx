import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useDeploymentMode } from "../auth/useDeploymentMode";
import { getOrganizationQuota } from "../api/endpoints";
import { OrganizationQuotaCard } from "./OrganizationQuotaCard";
import { formatUploadLimit, MAX_UPLOAD_BYTES } from "../lib/uploads";

vi.mock("../auth/useDeploymentMode");
vi.mock("../api/endpoints", () => ({ getOrganizationQuota: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it.each([
  [MAX_UPLOAD_BYTES, "25 MiB"],
  [1.5 * 1024 ** 2, "1,5 MiB"],
  [1_294_537, "1,23 MiB"],
  [1.125 * 1024 ** 2, "1,13 MiB"],
  [2 * 1024 ** 3, "2.048 MiB"],
])("formats %i bytes with at most two German decimal places", (bytes, expected) => {
  expect(formatUploadLimit(bytes)).toBe(expected);
});

it("uses the shared size formatter for hosted storage usage and limits", async () => {
  vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", error: false, mailAvailable: false });
  vi.mocked(getOrganizationQuota).mockResolvedValue({
    plan: "free",
    usage: { storage_bytes: 1_294_537, properties: 1, units: 2, ai_budget_usd: "0" },
    limits: { storage_bytes: 2 * 1024 ** 3, properties: 20, units: 200, upload_bytes: MAX_UPLOAD_BYTES, ai_budget_usd: "0" },
  });
  render(<OrganizationQuotaCard />);
  expect(await screen.findByText(`${formatUploadLimit(1_294_537)} / ${formatUploadLimit(2 * 1024 ** 3)}`)).toBeVisible();
});

it.each(["selfhost", null] as const)("renders nothing and skips quota requests while mode is %s", mode => {
  vi.mocked(useDeploymentMode).mockReturnValue({ mode, error: false, mailAvailable: false });
  vi.mocked(getOrganizationQuota).mockResolvedValue({} as never);
  const { container } = render(<OrganizationQuotaCard />);
  expect(container).toBeEmptyDOMElement();
  expect(getOrganizationQuota).not.toHaveBeenCalled();
  expect(screen.queryByText("Kontingent wird geladen …")).toBeNull();
});
