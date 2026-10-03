import { api } from './client';
import type { WegRun } from './types';

export interface PlanSuccessorPreview {
  run_id: string; current_plan_id: string; successor_id: string | null;
  plan_year: number; period_start: string; period_end: string; monthly_due_day: number | null;
}
export interface PlanSuccessorSelection {
  expected_plan_id: string; successor_id: string; reason: string; confirmed: true;
}
export const financialCorrectionsApi = {
  previewPlan: (id: string, signal?: AbortSignal) => api.get<PlanSuccessorPreview>(`/api/weg-settlements/${id}/economic-plan-successor`, { signal }),
  selectPlan: (id: string, body: PlanSuccessorSelection) => api.patch<WegRun>(`/api/weg-settlements/${id}/economic-plan`, body),
};
