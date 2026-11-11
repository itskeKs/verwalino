import { describe, expect, it } from "vitest";
import { PAYMENT_TYPE_LABELS } from "./paymentTypes";

describe("payment type labels", () => {
  it("spell the reserve types with umlauts", () => {
    expect(PAYMENT_TYPE_LABELS.RUECKLAGENANTEIL).toBe("Rücklagenanteil");
    expect(PAYMENT_TYPE_LABELS.RUECKLAGENENTNAHME).toBe("Rücklagenentnahme");
  });
});
