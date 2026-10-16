/**
 * Tests for costVocabulary.ts — validates that UI values are correctly
 * converted to DB values (and back) as required by T02.
 */

import { describe, it, expect } from "vitest";
import {
  toDbApportionability,
  fromDbApportionability,
  toDbDistributionKey,
  fromDbDistributionKey,
  APPORTIONABILITY_LABELS,
  DISTRIBUTION_KEY_LABELS,
} from "../lib/costVocabulary";

describe("costVocabulary", () => {
  describe("apportionability labels", () => {
    it("has entries for full, partial, none", () => {
      expect(APPORTIONABILITY_LABELS.full).toBe("Voll umlagefaehig");
      expect(APPORTIONABILITY_LABELS.partial).toBe("Teilweise umlagefaehig");
      expect(APPORTIONABILITY_LABELS.none).toBe("Nicht umlagefaehig");
    });

    it("has entries for additional DB values", () => {
      expect(APPORTIONABILITY_LABELS.extern_pruefen).toBe("Umlagefaehig extern pruefen");
      expect(APPORTIONABILITY_LABELS.gemischt_pruefen).toBe("Gemischt pruefen");
      expect(APPORTIONABILITY_LABELS.manuell_pruefen).toBe("Manuell pruefen");
    });
  });

  describe("toDbApportionability", () => {
    it("converts full to UMLAGEFAEHIG", () => {
      expect(toDbApportionability("full")).toBe("UMLAGEFAEHIG");
    });

    it("converts partial to TEILWEISE_PRUEFEN", () => {
      expect(toDbApportionability("partial")).toBe("TEILWEISE_PRUEFEN");
    });

    it("converts none to NICHT_UMLAGEFAEHIG", () => {
      expect(toDbApportionability("none")).toBe("NICHT_UMLAGEFAEHIG");
    });

    it("passes through unknown DB values unchanged", () => {
      expect(toDbApportionability("GEMISCHT_PRUEFEN")).toBe("GEMISCHT_PRUEFEN");
    });
  });

  describe("fromDbApportionability", () => {
    it("converts UMLAGEFAEHIG to full", () => {
      expect(fromDbApportionability("UMLAGEFAEHIG")).toBe("full");
    });

    it("converts TEILWEISE_PRUEFEN to partial", () => {
      expect(fromDbApportionability("TEILWEISE_PRUEFEN")).toBe("partial");
    });

    it("converts NICHT_UMLAGEFAEHIG to none", () => {
      expect(fromDbApportionability("NICHT_UMLAGEFAEHIG")).toBe("none");
    });

    it("passes through unknown DB values unchanged", () => {
      expect(fromDbApportionability("IRGENDWAS")).toBe("IRGENDWAS");
    });
  });

  describe("distribution key labels", () => {
    it("has entries for all keys", () => {
      expect(DISTRIBUTION_KEY_LABELS[""]).toBe("(kein)");
      expect(DISTRIBUTION_KEY_LABELS.MEA).toBe("MEA");
      expect(DISTRIBUTION_KEY_LABELS.AREA).toBe("Flaeche");
      expect(DISTRIBUTION_KEY_LABELS.HEATED_AREA).toBe("Beheizte Flaeche");
      expect(DISTRIBUTION_KEY_LABELS.UNIT_COUNT).toBe("Einheiten");
      expect(DISTRIBUTION_KEY_LABELS.PERSON_COUNT).toBe("Personen");
      expect(DISTRIBUTION_KEY_LABELS.CONSUMPTION).toBe("Verbrauch");
      expect(DISTRIBUTION_KEY_LABELS.EXTERNAL).toBe("Extern");
      expect(DISTRIBUTION_KEY_LABELS.MANUAL).toBe("Manuell");
    });
  });

  describe("toDbDistributionKey", () => {
    it("converts AREA to WOHNFLAECHE", () => {
      expect(toDbDistributionKey("AREA")).toBe("WOHNFLAECHE");
    });

    it("converts HEATED_AREA to BEHEIZTE_FLAECHE", () => {
      expect(toDbDistributionKey("HEATED_AREA")).toBe("BEHEIZTE_FLAECHE");
    });

    it("converts UNIT_COUNT to EINHEIT", () => {
      expect(toDbDistributionKey("UNIT_COUNT")).toBe("EINHEIT");
    });

    it("converts PERSON_COUNT to PERSONENZAHL", () => {
      expect(toDbDistributionKey("PERSON_COUNT")).toBe("PERSONENZAHL");
    });

    it("converts CONSUMPTION to VERBRAUCH", () => {
      expect(toDbDistributionKey("CONSUMPTION")).toBe("VERBRAUCH");
    });

    it("converts EXTERNAL to EXTERNE_ABRECHNUNG", () => {
      expect(toDbDistributionKey("EXTERNAL")).toBe("EXTERNE_ABRECHNUNG");
    });

    it("converts MANUAL to MANUELLER_BETRAG", () => {
      expect(toDbDistributionKey("MANUAL")).toBe("MANUELLER_BETRAG");
    });

    it("passes through MEA and empty string unchanged", () => {
      expect(toDbDistributionKey("MEA")).toBe("MEA");
      expect(toDbDistributionKey("")).toBe("");
    });
  });

  describe("fromDbDistributionKey", () => {
    it("converts WOHNFLAECHE to AREA", () => {
      expect(fromDbDistributionKey("WOHNFLAECHE")).toBe("AREA");
    });

    it("converts BEHEIZTE_FLAECHE to HEATED_AREA", () => {
      expect(fromDbDistributionKey("BEHEIZTE_FLAECHE")).toBe("HEATED_AREA");
    });

    it("converts EINHEIT to UNIT_COUNT", () => {
      expect(fromDbDistributionKey("EINHEIT")).toBe("UNIT_COUNT");
    });

    it("converts PERSONENZAHL to PERSON_COUNT", () => {
      expect(fromDbDistributionKey("PERSONENZAHL")).toBe("PERSON_COUNT");
    });

    it("converts VERBRAUCH to CONSUMPTION", () => {
      expect(fromDbDistributionKey("VERBRAUCH")).toBe("CONSUMPTION");
    });

    it("converts EXTERNE_ABRECHNUNG to EXTERNAL", () => {
      expect(fromDbDistributionKey("EXTERNE_ABRECHNUNG")).toBe("EXTERNAL");
    });

    it("converts MANUELLER_BETRAG to MANUAL", () => {
      expect(fromDbDistributionKey("MANUELLER_BETRAG")).toBe("MANUAL");
    });

    it("passes through MEA and empty string unchanged", () => {
      expect(fromDbDistributionKey("MEA")).toBe("MEA");
      expect(fromDbDistributionKey("")).toBe("");
    });
  });

  describe("round-trip conversion", () => {
    it("UI value → DB → UI returns original for all apportionability values", () => {
      for (const ui of ["full", "partial", "none"]) {
        expect(fromDbApportionability(toDbApportionability(ui))).toBe(ui);
      }
    });

    it("UI value → DB → UI returns original for all distribution keys", () => {
      for (const ui of ["", "MEA", "AREA", "HEATED_AREA", "UNIT_COUNT", "PERSON_COUNT", "CONSUMPTION", "EXTERNAL", "MANUAL"]) {
        expect(fromDbDistributionKey(toDbDistributionKey(ui))).toBe(ui);
      }
    });
  });
});
