import { expect, it } from "vitest";

// Issue #48: every money, date, datetime and IBAN call site renders through lib/format.ts
// (DESIGN.md "Number, money, date and size formats"). These are the hand-rolled forms that
// drifted before; a new occurrence is a call site that bypasses the helpers.
const sources = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!./format.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
});

const dateFields = "finalized_at|released_at|revoked_at|retention_until|governing_retention_until|meeting_date|reference_date|reading_date|previous_valid_from|key_date|valid_from|valid_to|value_date|booking_date|invoice_date|due_date|issue_date|day";
const bypasses: [string, RegExp][] = [
  ["a hand-rolled TT.MM.JJJJ date", /\.split\(["']-["']\)\.reverse\(\)\.join\(["']\.["']\)/],
  ["toLocaleDateString", /toLocaleDateString/],
  ["a timestamp cut to its ISO date", /_at\.slice\(0, ?10\)/],
  ["an amount followed by EUR", /\}\s+EUR\b|&&\s*["']EUR["']/],
  ["a raw date or timestamp field", new RegExp(`(?<![=\\w])\\$?\\{\\s*[\\w.?]+\\.(?:${dateFields})\\s*\\}`)],
];

// Keys and accessible names carry machine values, not displayed text.
const displayed = (text: string) => text.replace(/(?:key|aria-label)=\{`[^`]*`\}/g, "").replace(/key=\{[^{}]*\}/g, "");

it.each(bypasses)("renders no call site with %s", (_, pattern) => {
  const offenders = Object.entries(sources)
    .filter(([, text]) => pattern.test(displayed(text)))
    .map(([path]) => path);
  expect(offenders).toEqual([]);
});

it("scans the application sources", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(100);
  expect(Object.keys(sources).some((path) => path.endsWith("/lib/format.ts"))).toBe(false);
});
