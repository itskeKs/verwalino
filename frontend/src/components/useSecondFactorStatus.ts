import { useCallback, useEffect, useState } from "react";
import * as endpoints from "../api/endpoints";

export function useSecondFactorStatus() {
  const [enrolled, setEnrolled] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    setError(null);
    endpoints.getSecondFactorStatus()
      .then(answer => setEnrolled(answer.enrolled))
      .catch(() => setError("Der Status des zweiten Faktors konnte nicht geladen werden."));
  }, []);
  useEffect(() => reload(), [reload]);
  return { enrolled, error, reload };
}
