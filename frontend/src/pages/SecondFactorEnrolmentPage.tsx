/**
 * Hosted enrolment wizard (issue #208, owner decision of 5 Nov 2026):
 * a hosted staff account without a confirmed factor sees only this page
 * until the enrolment is complete. The backend restricts the session to
 * the second-factor routes; confirming reissues a full session cookie.
 */

import { PublicAuthLayout } from "../components/PublicAuthLayout";
import { SecondFactorEnrolment } from "../components/SecondFactorEnrolment";
import { useAuth } from "../auth/useAuth";

export function SecondFactorEnrolmentPage() {
  const { reloadSession } = useAuth();
  return (
    <PublicAuthLayout
      title="Zweiten Faktor einrichten"
      description="Für Ihr Benutzerkonto ist ein zweiter Faktor vorgeschrieben. Richten Sie ihn jetzt ein, um verwalino zu nutzen."
    >
      <SecondFactorEnrolment onConfirmed={() => void reloadSession()} />
    </PublicAuthLayout>
  );
}
