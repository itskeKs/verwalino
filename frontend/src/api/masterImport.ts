import { api } from "./client";
import type { DeclarationApprovalRequest } from "./types";
export type NewPropertyAudience = NonNullable<DeclarationApprovalRequest["new_property_audience"]>;
export type MasterImportKind = "property" | "unit" | "person";
export interface MasterImportInput { csv_text: string; data_audience: NewPropertyAudience | null }
export interface MasterImportApplyInput extends MasterImportInput { expected_digest: string; request_id: string }
export interface MasterImportCounts { properties: number; units: number; persons: number }
export interface MasterImportPreview {
  rows: { line: number; kind: MasterImportKind; key: string; values: Record<string, string> }[];
  errors: { line: number; field: string; message: string }[];
  counts: MasterImportCounts;
  source_digest: string;
}
export interface MasterImportResult {
  rows: { line: number; kind: MasterImportKind; key: string; id: string }[];
  counts: MasterImportCounts;
}
export async function readMasterImportFile(file: File): Promise<string> {
  if (file.size > 256 * 1024) throw new Error("Die CSV-Datei darf höchstens 256 KiB groß sein.");
  const bytes = await new Promise<ArrayBuffer>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Die CSV-Datei konnte nicht gelesen werden."));
    reader.onabort = () => reject(new Error("Das Lesen der CSV-Datei wurde abgebrochen."));
    reader.onload = () => {
      if (reader.result instanceof ArrayBuffer) resolve(reader.result);
      else reject(new Error("Die CSV-Datei konnte nicht gelesen werden."));
    };
    reader.readAsArrayBuffer(file);
  });
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("Die CSV-Datei muss gültiges UTF-8 enthalten (BOM erlaubt).");
  }
}

export function previewMasterImport(input: MasterImportInput, signal?: AbortSignal): Promise<MasterImportPreview> {
  return api.post<MasterImportPreview>("/api/properties/import/preview", input, { signal });
}

export function applyMasterImport(input: MasterImportApplyInput, signal?: AbortSignal): Promise<MasterImportResult> {
  return api.post<MasterImportResult>("/api/properties/import/apply", input, { signal });
}
