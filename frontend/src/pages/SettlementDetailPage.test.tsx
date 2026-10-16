/**
 * Tests for SettlementDetailPage.tsx — validates that BK-Zusatzeingaben
 * (needsConsumption, needsPersonCount, needsExternalInput, needsManualInput)
 * are correctly detected from cost entries with DB values.
 *
 * T03: "Wasser nach Verbrauch, Heizung extern eingeben, berechnen, finalisieren, PDF öffnen".
 */

import { describe, it, expect } from "vitest";
import {
  needsConsumption,
  needsPersonCount,
  needsExternalInput,
  needsManualInput,
} from "./SettlementDetailPage";

// Helper to create a minimal CostEntry for testing
function createCostEntry(overrides: Record<string, unknown> = {}): any {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    tenant_distribution_key: "",
    ...overrides,
  };
}

describe("SettlementDetailPage needs* functions", () => {
  describe("needsConsumption", () => {
    it("returns true when entries contain CONSUMPTION", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "CONSUMPTION" })];
      expect(needsConsumption(entries)).toBe(true);
    });

    it("returns false when no entries have CONSUMPTION", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "MEA" })];
      expect(needsConsumption(entries)).toBe(false);
    });

    it("returns true if any entry has CONSUMPTION among many", () => {
      const entries = [
        createCostEntry({ tenant_distribution_key: "MEA" }),
        createCostEntry({ tenant_distribution_key: "CONSUMPTION" }),
        createCostEntry({ tenant_distribution_key: "PERSON_COUNT" }),
      ];
      expect(needsConsumption(entries)).toBe(true);
    });
  });

  describe("needsPersonCount", () => {
    it("returns true when entries contain PERSON_COUNT", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "PERSON_COUNT" })];
      expect(needsPersonCount(entries)).toBe(true);
    });

    it("returns false when no entries have PERSON_COUNT", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "MEA" })];
      expect(needsPersonCount(entries)).toBe(false);
    });
  });

  describe("needsExternalInput", () => {
    it("returns true when entry has EXTERNAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "EXTERNAL" });
      expect(needsExternalInput(entry)).toBe(true);
    });

    it("returns false when entry does not have EXTERNAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MEA" });
      expect(needsExternalInput(entry)).toBe(false);
    });
  });

  describe("needsManualInput", () => {
    it("returns true when entry has MANUAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MANUAL" });
      expect(needsManualInput(entry)).toBe(true);
    });

    it("returns false when entry does not have MANUAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MEA" });
      expect(needsManualInput(entry)).toBe(false);
    });
  });
});
