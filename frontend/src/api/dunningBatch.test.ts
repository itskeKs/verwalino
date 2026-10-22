import { afterEach, expect, it, vi } from "vitest";
import { api } from "./client";
import { previewDunningBatch, createDunningBatch, sumBatchAmounts } from "./dunningBatch";

afterEach(() => vi.restoreAllMocks());
const input = { period_start: "2026-01-01", period_end: "2026-12-31", reference_date: "2026-10-01", threshold: "0.01", grace_days: 0, base_interest_rate: null };
it("posts exact decimal text and explicit null interest without adding a fee override", async () => {
  const post = vi.spyOn(api, "post").mockResolvedValue({ candidates: [], source_digest: "digest" });
  await previewDunningBatch(input);
  expect(post).toHaveBeenCalledWith("/api/dunning/batch/preview", input);
  const body = { ...input, lease_agreement_ids: ["lease"], expected_source_digest: "digest", request_id: "uuid" };
  await createDunningBatch(body);
  expect(post).toHaveBeenLastCalledWith("/api/dunning/batch", body);
});
it("sums amounts exactly even above binary floating point precision", () => {
  expect(sumBatchAmounts(["90071992547409.91", "0.02", "-0.01"])).toBe("90071992547409.92");
  expect(() => sumBatchAmounts(["bad"])).toThrow();
});
