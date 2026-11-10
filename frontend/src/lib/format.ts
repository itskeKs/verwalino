/**
 * Shared display formats (DESIGN.md "Number, money, date and size formats").
 * Money renders from integer cents so amounts never pass through floating point.
 */

/** Convert an API decimal string such as "1234.56" (or "-119.01") to exact integer cents. */
export function centsFromDecimal(value: string): bigint {
  const [whole = "0", fraction = ""] = value.split(".");
  const negative = whole.startsWith("-");
  const absolute = BigInt(whole.replace("-", "") || "0") * 100n + BigInt((fraction + "00").slice(0, 2));
  return negative ? -absolute : absolute;
}

/** Money contract `1.234,56 €`: dot thousands, comma decimals, two digits, sign first. */
export function formatMoney(cents: bigint | number): string {
  const value = typeof cents === "bigint" ? cents : BigInt(Math.trunc(cents));
  const absolute = value < 0n ? -value : value;
  const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value < 0n ? "-" : ""}${whole},${(absolute % 100n).toString().padStart(2, "0")} €`;
}

/** Date contract `TT.MM.JJJJ` from the ISO date part of an API value. */
export function formatDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : iso;
}

/** Date and time contract `TT.MM.JJJJ, HH:MM Uhr` in local time, without seconds. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}, ${pad(date.getHours())}:${pad(date.getMinutes())} Uhr`;
}

/** IBAN contract: group the stored value in blocks of four. */
export function formatIban(iban: string): string {
  return iban.replace(/\s/g, "").replace(/(.{4})/g, "$1 ").trim();
}
