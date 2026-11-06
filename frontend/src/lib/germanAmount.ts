/** Parse cent-exact EUR input, including German grouping and API decimal strings. */
export function parseGermanAmount(input: string): string | null {
  const value = input.trim();
  const grouped = /^-?\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?$/.test(value);
  if (!grouped && !/^-?\d+(?:[.,]\d{1,2})?$/.test(value)) return null;
  const normalized = (grouped ? value.replaceAll(".", "") : value).replace(",", ".");
  const [whole, fraction = ""] = normalized.replace(/^-/, "").split(".");
  const integer = whole.replace(/^0+(?=\d)/, "");
  if (integer.length > 12) return null;
  return `${normalized.startsWith("-") ? "-" : ""}${integer}.${fraction.padEnd(2, "0")}`;
}
