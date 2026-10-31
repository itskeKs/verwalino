/** Recurring month/day pattern only; concrete year bounds are supplied by the API. */
export function settlementPeriodPattern(startMonth: number): string {
  const last = new Date(Date.UTC(2001, startMonth - 1, 0));
  const two = (n: number) => String(n).padStart(2, "0");
  return `01.${two(startMonth)}. bis ${two(last.getUTCDate())}.${two(last.getUTCMonth() + 1)}.`;
}
