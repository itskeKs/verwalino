import type { InvoiceDocument } from "../api/types";
import { INVOICE_STATUS_LABELS, invoiceProcessingMessage } from "../lib/invoiceProcessing";

export function InvoiceProcessingStatus({ document }: { document: InvoiceDocument }) {
  return <span role="status">{INVOICE_STATUS_LABELS[document.processing_status] ?? INVOICE_STATUS_LABELS.legacy}</span>;
}

/** What happened to the invoice and what the user can do, from the one map of reasons; nothing while nothing went wrong. */
export function InvoiceProcessingExplanation({ document }: { document: InvoiceDocument }) {
  const text = invoiceProcessingMessage(document.processing_status, document.processing_error_code);
  return text ? <p className="text-sm break-words max-w-prose mt-2 mb-3">{text}</p> : null;
}
