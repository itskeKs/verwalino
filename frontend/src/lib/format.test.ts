/// <reference types="node" />
import { describe, expect, it } from "vitest";
import { centsFromDecimal, formatDate, formatDateTime, formatIban, formatMoney } from "./format";

process.env.TZ = "UTC";

describe("formatMoney", () => {
  it("groups thousands with a dot and suffixes the euro sign", () => {
    expect(formatMoney(123456n)).toBe("1.234,56 €");
    expect(formatMoney(225000n)).toBe("2.250,00 €");
  });

  it("always prints two decimals", () => {
    expect(formatMoney(70000n)).toBe("700,00 €");
    expect(formatMoney(5n)).toBe("0,05 €");
    expect(formatMoney(0n)).toBe("0,00 €");
  });

  it("puts the sign before the grouped amount", () => {
    expect(formatMoney(-10000n)).toBe("-100,00 €");
    expect(formatMoney(-123456n)).toBe("-1.234,56 €");
  });

  it("accepts plain number cents without float arithmetic", () => {
    expect(formatMoney(576000)).toBe("5.760,00 €");
  });
});

describe("centsFromDecimal", () => {
  it("converts API decimal strings exactly", () => {
    expect(centsFromDecimal("1234.56")).toBe(123456n);
    expect(centsFromDecimal("0.01")).toBe(1n);
    expect(centsFromDecimal("0.5")).toBe(50n);
  });

  it("keeps negative amounts exact", () => {
    expect(centsFromDecimal("-119.01")).toBe(-11901n);
  });
});

describe("formatDate", () => {
  it("renders zero-padded TT.MM.JJJJ from the ISO date", () => {
    expect(formatDate("2026-03-05")).toBe("05.03.2026");
    expect(formatDate("2026-01-01")).toBe("01.01.2026");
    expect(formatDate("2026-11-30")).toBe("30.11.2026");
  });

  it("uses only the date part of a timestamp", () => {
    expect(formatDate("2026-03-05T14:05:00")).toBe("05.03.2026");
  });
});

describe("formatDateTime", () => {
  it("renders date and HH:MM Uhr without seconds", () => {
    expect(formatDateTime("2026-10-31T14:05:00+01:00")).toBe("31.10.2026, 13:05 Uhr");
  });

  it("zero-pads single-digit day, month, hour and minute", () => {
    expect(formatDateTime("2026-01-01T04:05:09Z")).toBe("01.01.2026, 04:05 Uhr");
  });
});

describe("formatIban", () => {
  it("groups the stored IBAN in blocks of four", () => {
    expect(formatIban("DE75512108001245126199")).toBe("DE75 5121 0800 1245 1261 99");
  });

  it("accepts already grouped input", () => {
    expect(formatIban("DE75 5121 0800 1245 1261 99")).toBe("DE75 5121 0800 1245 1261 99");
  });
});
