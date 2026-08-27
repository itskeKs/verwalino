/**
 * Shared badge for cost entry source (Quelle).
 *
 * Renders a human-readable label with tone:
 *   E_INVOICE  -> "E-Rechnung" (+ format hint when available), tone=primary
 *   PDF_IMPORT -> "PDF-Import", tone=info
 *   MANUAL     -> "Manuell", tone=default
 *   fallback   -> raw value, tone=outline
 */

import { Badge, type BadgeTone } from "./ui/badge";

const SOURCE_META: Record<string, { label: string; tone: BadgeTone }> = {
  E_INVOICE: { label: "E-Rechnung", tone: "primary" },
  PDF_IMPORT: { label: "PDF-Import", tone: "info" },
  MANUAL: { label: "Manuell", tone: "default" },
};

/** Human-readable short label for source_format values from the e-invoice parser. */
const FORMAT_LABELS: Record<string, string> = {
  XRECHNUNG_UBL: "XRechnung",
  XRECHNUNG_CII: "XRechnung",
  ZUGFERD: "ZUGFeRD",
};

interface SourceBadgeProps {
  source: string;
  /** Optional e-invoice source_format (e.g. "XRECHNUNG_UBL", "ZUGFERD"). */
  sourceFormat?: string | null;
}

export function SourceBadge({ source, sourceFormat }: SourceBadgeProps) {
  const meta = SOURCE_META[source];
  const label = meta?.label ?? source;
  const tone = meta?.tone ?? "outline";

  // For E_INVOICE, append the format if known
  const formatHint =
    source === "E_INVOICE" && sourceFormat
      ? FORMAT_LABELS[sourceFormat] ?? sourceFormat
      : null;

  return (
    <Badge tone={tone}>
      {label}
      {formatHint && (
        <span style={{ opacity: 0.7, marginLeft: "0.25em" }}>
          ({formatHint})
        </span>
      )}
    </Badge>
  );
}
