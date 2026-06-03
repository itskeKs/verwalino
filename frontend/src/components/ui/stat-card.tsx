import { type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { cn } from "../../lib/utils";

type Tone = "default" | "primary" | "success" | "warning" | "destructive";

const ICON_TONES: Record<Tone, string> = {
  default: "bg-slate-100 text-slate-600",
  primary: "bg-blue-100 text-blue-600",
  success: "bg-emerald-100 text-emerald-600",
  warning: "bg-amber-100 text-amber-600",
  destructive: "bg-red-100 text-red-600",
};

interface Props {
  label: string;
  value: ReactNode;
  icon: LucideIcon;
  tone?: Tone;
  hint?: string;
  to?: string;
}

export function StatCard({ label, value, icon: Icon, tone = "default", hint, to }: Props) {
  const inner = (
    <div
      className={cn(
        "h-full rounded-lg border border-border bg-card p-5 shadow-sm transition-shadow",
        to && "cursor-pointer hover:shadow-md hover:border-primary/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span
          className={cn(
            "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
            ICON_TONES[tone],
          )}
        >
          <Icon className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
  return to ? (
    <Link to={to} className="block">
      {inner}
    </Link>
  ) : (
    inner
  );
}
