// Normalize form input only; rent and cost calculations stay in the backend.
export function rentAmount(value: string): string | null {
  const trimmed = value.trim();
  const normalized = /^\d{1,3}(?:\.\d{3})+,\d{1,2}$/.test(trimmed)
    ? trimmed.replaceAll(".", "") : trimmed;
  const match = /^(\d+)(?:[.,](\d{1,2}))?$/.exec(normalized);
  if (!match) return null;
  const integer = match[1].replace(/^0+(?=\d)/, "");
  if (integer.length > 12) return null;
  return `${integer}.${(match[2] ?? "").padEnd(2, "0")}`;
}
