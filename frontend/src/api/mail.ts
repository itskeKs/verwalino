import { api } from "./client";

export type MailKind = "dunning" | "settlement" | "invitation";
export interface MailRecipient {
  person_id: string;
  name: string;
  email: string | null;
}
export interface MailAttempt {
  id: string;
  person_id: string;
  email: string;
  status: "prepared" | "accepted" | "failed" | "unknown";
  created_at: string;
  completed_at: string | null;
}
export interface MailDispatchHistory {
  recipients: MailRecipient[];
  attempts: MailAttempt[];
}
const url = (kind: MailKind, documentId: string) => `/api/mail/${kind}/${encodeURIComponent(documentId)}`;
export function getMailDispatch(kind: MailKind, documentId: string, signal?: AbortSignal) {
  return api.get<MailDispatchHistory>(url(kind, documentId), { signal });
}
export function sendMailDispatch(kind: MailKind, documentId: string, personId: string, requestId: string) {
  return api.post<MailAttempt>(url(kind, documentId), { person_id: personId, request_id: requestId });
}
