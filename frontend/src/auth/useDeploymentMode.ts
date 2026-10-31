import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type DeploymentMode = "selfhost" | "hosted";
type Metadata = { deployment_mode: DeploymentMode; version: string };

export function useDeploymentMode() {
  const [state, setState] = useState<{ mode: DeploymentMode | null; error: boolean }>({ mode: null, error: false });
  const pending = useRef<Promise<Metadata> | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Reuse the request during StrictMode's effect replay as well as rerenders.
    pending.current ??= api.get<Metadata>("/api/meta", { cache: "no-store" });
    pending.current.then(
      (answer) => {
        if (cancelled) return;
        if (answer.deployment_mode === "selfhost" || answer.deployment_mode === "hosted") {
          setState({ mode: answer.deployment_mode, error: false });
        } else {
          setState({ mode: null, error: true });
        }
      },
      () => { if (!cancelled) setState({ mode: null, error: true }); },
    );
    return () => { cancelled = true; };
  }, []);

  return state;
}
