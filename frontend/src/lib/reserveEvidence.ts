import type { CostEntry, RegisterEntry } from '../api/types';

export const reserveCostOptions = (rows: CostEntry[], selected: string) =>
  rows.filter(row => row.id === selected || (row.status === 'confirmed' && !row.superseded_by_id));

export const reserveResolutionOptions = (rows: RegisterEntry[], selected: string) =>
  rows.filter(row => row.id === selected || row.result === 'ANGENOMMEN');
