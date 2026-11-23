/** The word that opens an issue line of the declaration review, by severity. */
export function issuePrefix(severity: string): string {
  return { INFO: "Hinweis", WARNING: "Prüfen", BLOCKER: "Freigabe gesperrt" }[severity] ?? severity;
}
