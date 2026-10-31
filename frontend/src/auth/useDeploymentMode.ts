import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type DeploymentMode = "selfhost" | "hosted";
type Metadata = { deployment_mode: DeploymentMode; version: string; mail_available?: boolean };

export function useDeploymentMode() {
  const [state, setState] = useState<{ mode: DeploymentMode | null; error: boolean; mailAvailable: boolean }>({ mode: null, error: false, mailAvailable: false });
  const pending = useRef<Promise<Metadata> | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Reuse the request during StrictMode's effect replay as well as rerenders.
    pending.current ??= api.get<Metadata>("/api/meta", { cache: "no-store" });
    pending.current.then(
      (answer) => {
        if (cancelled) return;
        if (answer.deployment_mode === "selfhost" || answer.deployment_mode === "hosted") {
          setState({ mode: answer.deployment_mode, error: false, mailAvailable: answer.mail_available === true });
        } else {
          setState({ mode: null, error: true, mailAvailable: false });
        }
      },
      () => { if (!cancelled) setState({ mode: null, error: true, mailAvailable: false }); },
    );
    return () => { cancelled = true; };
  }, []);

  return state;
}
