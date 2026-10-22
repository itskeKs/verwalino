import { api } from "./client";
import type { DunningNotice } from "./types";

export interface DunningBatchInput {
  period_start: string;
  period_end: string;
  reference_date: string;
  threshold: string;
  grace_days: number;
  base_interest_rate: string | null;
  accounting_scope_id?: string;
}

export interface DunningBatchCandidate {
  lease_agreement_id: string;
  tenant_profile_id: string;
  person_name: string;
  unit_number: string;
  property_name: string;
  principal_amount: string;
  fee_amount: string;
  interest_amount: string;
  total_amount: string;
  level: number;
}

export interface DunningBatchPreview {
  candidates: DunningBatchCandidate[];
  source_digest: string;
}

export interface DunningBatchCreate extends DunningBatchInput {
  lease_agreement_ids: string[];
  expected_source_digest: string;
  request_id: string;
}

export function previewDunningBatch(input: DunningBatchInput): Promise<DunningBatchPreview> {
  return api.post("/api/dunning/batch/preview", input);
}

export function createDunningBatch(input: DunningBatchCreate): Promise<DunningNotice[]> {
  return api.post("/api/dunning/batch", input);
}

/** API money remains decimal text, including when aggregating large balances. */
export function sumBatchAmounts(amounts: string[]): string {
  const cents = amounts.reduce((sum, value) => {
    const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value);
    if (!match) throw new Error("Ungültiger Geldbetrag in der Vorschau. Bitte erneut prüfen.");
    const amount = BigInt(match[2]) * 100n + BigInt((match[3] ?? "").padEnd(2, "0"));
    return sum + (match[1] ? -amount : amount);
  }, 0n);
  const absolute = cents < 0n ? -cents : cents;
  return `${cents < 0n ? "-" : ""}${absolute / 100n}.${String(absolute % 100n).padStart(2, "0")}`;
}
