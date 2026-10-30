import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export function useSetupStatus() {
  const [required, setRequired] = useState<boolean | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.get<{ required: boolean }>("/api/setup", { cache: "no-store" }).then(
      (answer) => { if (!cancelled) setRequired(answer.required); },
      () => { if (!cancelled) setError(true); },
    );
    return () => { cancelled = true; };
  }, [attempt]);

  function retry() {
    setError(false);
    setAttempt((previous) => previous + 1);
  }

  const markClosed = useCallback(() => setRequired(false), []);

  return { required, error, retry, markClosed };
}
