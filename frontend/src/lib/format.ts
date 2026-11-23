/**
 * Shared display formats (DESIGN.md "Number, money, date and size formats").
 * Money renders from integer cents so amounts never pass through floating
 * point; dates and times render on the Europe/Berlin calendar (DESIGN.md).
 */

const BERLIN_DATE = new Intl.DateTimeFormat("de-DE", {
  timeZone: "Europe/Berlin",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const BERLIN_DATE_TIME = new Intl.DateTimeFormat("de-DE", {
  timeZone: "Europe/Berlin",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

type BerlinParts = Record<"day" | "month" | "year" | "hour" | "minute", string>;

/** Parse an ISO value; returns null for input without a valid instant so callers can fall back. */
function parseInstant(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

function berlinParts(date: Date, formatter: Intl.DateTimeFormat): BerlinParts {
  const parts = {} as BerlinParts;
  for (const part of formatter.formatToParts(date)) {
    if (part.type !== "literal") parts[part.type as keyof BerlinParts] = part.value;
  }
  return parts;
}

/** Convert an API decimal string such as "1234.56" (or "-119.01") to exact integer cents.
 *  Fractions beyond two decimals round half away from zero, as the backend's round_money does. */
export function centsFromDecimal(value: string): bigint {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) throw new RangeError(`Not an API decimal amount: ${value}`);
  const fraction = (match[3] ?? "") + "000";
  let cents = BigInt(match[2]) * 100n + BigInt(fraction.slice(0, 2));
  if (Number(fraction[2]) >= 5) cents += 1n;
  return match[1] ? -cents : cents;
}

/** Money contract `1.234,56 €`: dot thousands, comma decimals, two digits, sign first. */
export function formatMoney(cents: bigint | number): string {
  const value = typeof cents === "bigint" ? cents : BigInt(Math.trunc(cents));
  const absolute = value < 0n ? -value : value;
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value < 0n ? "-" : ""}${whole},${(absolute % 100n).toString().padStart(2, "0")} €`;
}

/** Date contract `TT.MM.JJJJ`: a plain ISO date renders as stored; a timestamp renders its Europe/Berlin calendar date. */
export function formatDate(iso: string): string {
  const plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (plain) return `${plain[3]}.${plain[2]}.${plain[1]}`;
  if (!/^\d{4}-\d{2}-\d{2}T/.test(iso)) return iso;
  const instant = parseInstant(iso);
  if (!instant) return iso;
  const parts = berlinParts(instant, BERLIN_DATE);
  return `${parts.day}.${parts.month}.${parts.year}`;
}

/** Date and time contract `TT.MM.JJJJ, HH:MM Uhr` on the Europe/Berlin calendar, without seconds. */
export function formatDateTime(iso: string): string {
  const instant = parseInstant(iso);
  if (!instant) return iso;
  const parts = berlinParts(instant, BERLIN_DATE_TIME);
  return `${parts.day}.${parts.month}.${parts.year}, ${parts.hour}:${parts.minute} Uhr`;
}

/** Elapsed time `42 s`, `2 Min. 5 s`, `1 Std. 2 Min.`: whole seconds under a minute, minutes with seconds under an hour, then hours with minutes. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} Min. ${seconds % 60} s`;
  return `${Math.floor(seconds / 3600)} Std. ${Math.floor((seconds % 3600) / 60)} Min.`;
}

/** Percentage contract `19 %`: decimal comma, no trailing zeros, a normal space; a value that is not a plain decimal renders as stored. */
export function formatPercent(value: string): string {
  const match = /^(-?\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const fraction = (match[2] ?? "").replace(/0+$/, "");
  return `${match[1]}${fraction ? `,${fraction}` : ""} %`;
}

/** IBAN contract: group the stored value in blocks of four. */
export function formatIban(iban: string): string {
  return iban.replace(/\s/g, "").replace(/(.{4})/g, "$1 ").trim();
}

/** An amount as the API stores it (`"1234.56"`) in the money contract; a value that is not an API decimal throws, as `centsFromDecimal` does. */
export function formatDecimalMoney(value: string): string {
  return formatMoney(centsFromDecimal(value));
}

/** A proposal's stored value in a display format (``shown(value, formatDate)``); null stays null, and a value the format cannot read
 *  (an older proposal, a model's odd answer) is shown as stored. One wrapper for every screen that shows a proposal. */
export function shown(value: string | null | undefined, format: (value: string) => string): string | null {
  if (value == null) return null;
  try {
    return format(value);
  } catch {
    return value;
  }
}
