import { type HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export type BadgeTone =
  | "default"
  | "primary"
  | "success"
  | "warning"
  | "destructive"
  | "info"
  | "outline";

const TONES: Record<BadgeTone, string> = {
  default: "bg-secondary text-secondary-foreground",
  primary: "bg-blue-100 text-blue-700",
  success: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-800",
  destructive: "bg-red-100 text-red-700",
  info: "bg-slate-100 text-slate-600",
  outline: "border border-border text-foreground",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ className, tone = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}
