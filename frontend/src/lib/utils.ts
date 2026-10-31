import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class names, resolving conflicts (shadcn convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function canonicalValue(input: string): string | null {
  const trimmed = input.trim();
  const normalized = /^\d{1,3}(?:\.\d{3})+,\d{1,4}$/.test(trimmed)
    ? trimmed.replaceAll(".", "").replace(",", ".") : trimmed;
  const match = /^(\d+)(?:[.,](\d{1,4}))?$/.exec(normalized);
  if (!match) return null;
  const whole = match[1].replace(/^0+(?=\d)/, "");
  if (whole.length > 10) return null;
  return `${whole}.${(match[2] ?? "").padEnd(4, "0")}`;
}
