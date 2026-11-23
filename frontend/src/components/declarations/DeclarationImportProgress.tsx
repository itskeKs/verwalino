import { useEffect, useState } from "react";
import { Check, Circle, LoaderCircle } from "lucide-react";
import { formatElapsed } from "../../lib/format";
import type { DeclarationImportStep } from "../../api/types";

const STATE_WORDS: Record<DeclarationImportStep["state"], string> = {
  DONE: "Erledigt",
  RUNNING: "Läuft",
  WAITING: "Wartet",
};

// The server reports how long the running step has been going when it answers; the page counts on from that figure
// until the next answer, so that the time moves between two polls. `receivedAt` is when the answer arrived.
function RunningTime({ seconds, receivedAt }: { seconds: number; receivedAt: number }) {
  const [now, setNow] = useState(receivedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return <>{formatElapsed(seconds + Math.max(0, Math.floor((now - receivedAt) / 1_000)))}</>;
}

/** The steps of a started import as the server reported them: done, running (with its time) or waiting. */
export function DeclarationImportProgress({ steps, receivedAt }: { steps: DeclarationImportStep[]; receivedAt: number }) {
  return (
    <ol className="mt-4 grid gap-3 list-none p-0 m-0" aria-label="Verarbeitungsschritte">
      {steps.map((step) => (
        <li
          key={step.label}
          aria-current={step.state === "RUNNING" ? "step" : undefined}
          className="flex items-start gap-3 min-w-0"
        >
          <span
            aria-hidden="true"
            className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center ${
              step.state === "DONE" ? "text-emerald-700" : step.state === "RUNNING" ? "text-primary" : "text-muted-foreground"
            }`}
          >
            {step.state === "DONE" && <Check className="h-4 w-4" />}
            {step.state === "RUNNING" && <LoaderCircle className="h-4 w-4 animate-spin motion-reduce:animate-none" />}
            {step.state === "WAITING" && <Circle className="h-4 w-4" />}
          </span>
          <span className="min-w-0">
            <span className={`block text-sm break-words ${step.state === "WAITING" ? "text-muted-foreground" : "font-semibold"}`}>
              {step.label}
            </span>
            <span className="block text-sm text-muted-foreground break-words">
              {STATE_WORDS[step.state]}
              {step.state === "RUNNING" && step.elapsed_seconds !== null && <> seit <RunningTime seconds={step.elapsed_seconds} receivedAt={receivedAt} /></>}
              {step.detail && <> · {step.detail}</>}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
