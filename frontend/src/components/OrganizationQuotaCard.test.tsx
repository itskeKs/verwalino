import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useDeploymentMode } from "../auth/useDeploymentMode";
import { getOrganizationQuota } from "../api/endpoints";
import { OrganizationQuotaCard } from "./OrganizationQuotaCard";

vi.mock("../auth/useDeploymentMode");
vi.mock("../api/endpoints", () => ({ getOrganizationQuota: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it.each(["selfhost", null] as const)("renders nothing and skips quota requests while mode is %s", mode => {
  vi.mocked(useDeploymentMode).mockReturnValue({ mode, error: false, mailAvailable: false });
  vi.mocked(getOrganizationQuota).mockResolvedValue({} as never);
  const { container } = render(<OrganizationQuotaCard />);
  expect(container).toBeEmptyDOMElement();
  expect(getOrganizationQuota).not.toHaveBeenCalled();
  expect(screen.queryByText("Kontingent wird geladen …")).toBeNull();
});
