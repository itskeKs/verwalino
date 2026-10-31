import { api } from "./client";

export interface LetterTemplateInput {
  name: string;
  subject: string;
  body: string;
}
export interface LetterTemplate extends LetterTemplateInput {
  id: string;
  created_at: string;
  updated_at: string;
}
export interface LetterDocument {
  id: string;
  request_id: string;
  template_id: string | null;
  property_id: string;
  person_id: string;
  recipient_name: string;
  subject: string;
  as_of: string;
  created_at: string;
  pdf_url: string;
}
export interface LetterIssueInput {
  request_id: string;
  template_id: string;
  property_id: string;
  as_of: string;
}

const root = "/api/letters";
const templateBody = ({ name, subject, body }: LetterTemplateInput): LetterTemplateInput => ({ name, subject, body });
export function listLetterTemplates(signal?: AbortSignal) {
  return api.get<LetterTemplate[]>(`${root}/templates`, { signal });
}
export function createLetterTemplate(input: LetterTemplateInput) {
  return api.post<LetterTemplate>(`${root}/templates`, templateBody(input));
}
export function updateLetterTemplate(id: string, input: LetterTemplateInput) {
  return api.put<LetterTemplate>(`${root}/templates/${encodeURIComponent(id)}`, templateBody(input));
}
export function listLetterDocuments(propertyId: string, signal?: AbortSignal) {
  return api.get<LetterDocument[]>(`${root}/documents?${new URLSearchParams({ property_id: propertyId })}`, { signal });
}
export function issueLetters({ request_id, template_id, property_id, as_of }: LetterIssueInput) {
  return api.post<LetterDocument[]>(`${root}/issue`, { request_id, template_id, property_id, as_of });
}
