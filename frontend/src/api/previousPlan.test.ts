import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { createPreviousPlan, previewPreviousPlan, sumPlanAmounts, normalizePlanAmount } from "./previousPlan";

afterEach(() => vi.restoreAllMocks());
it("posts preview with independent source/read and target/prepare scopes", async () => {
  const post = vi.spyOn(api, "post").mockResolvedValue({});
  const input = { property_id: "property", plan_year: 2027, source_accounting_scope_id: "prior", accounting_scope_id: "future" };
  const controller = new AbortController();
  await previewPreviousPlan(input, controller.signal);
  expect(post).toHaveBeenCalledWith("/api/economic-plans/previous/preview", input, { signal: controller.signal });
});
it("posts creation with unchanged category metadata and explicit null/omitted scopes", async () => {
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "new" });
  const body = { property_id: "property", plan_year: 2027, source_accounting_scope_id: null,
    expected_source_digest: "digest", planned_costs: [{ category_id: "cat", category_code: "100", label: "Heizung", planned_amount: "7000.10", distribution_key: "MEA" as const, cost_group: "UMLAGEFAEHIG" as const }], reserve_contribution_total: "6000.00" };
  await createPreviousPlan(body);
  expect(post).toHaveBeenCalledWith("/api/economic-plans/previous", body);
  expect(post.mock.calls[0][1]).not.toHaveProperty("accounting_scope_id");
});
it("preserves distinct scopes on creation without adding period or compute fields", async () => {
  const post = vi.spyOn(api, "post").mockResolvedValue({ id: "new" });
  const body = { property_id: "property", plan_year: 2027, source_accounting_scope_id: "prior-read", accounting_scope_id: "future-prepare", expected_source_digest: "digest", planned_costs: [], reserve_contribution_total: "6000.00" };
  await createPreviousPlan(body);
  expect(post).toHaveBeenCalledWith("/api/economic-plans/previous", body);
});
it("adds money exactly beyond floating-point precision", () => {
  expect(sumPlanAmounts(["9007199254740993.01", "0.09"])).toBe("9007199254740993.10");
  expect(normalizePlanAmount(" 7000,1 ")).toBe("7000.10");
});
it.each(["", "-1", "0.001", "NaN", "Infinity", "1e3"])("rejects invalid money %s", value => {
  expect(normalizePlanAmount(value)).toBeNull();
});
