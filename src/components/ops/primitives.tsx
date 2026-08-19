import { forwardRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export type Tone = "neutral" | "critical" | "warning" | "ok" | "selected" | "freight" | "dim";

export function Panel({
  children,
  className,
  as: As = "section",
}: {
  children: ReactNode;
  className?: string | undefined;
  as?: "section" | "div" | "aside";
}) {
  return (
    <As className={cn("flex min-h-0 flex-col bg-panel", className)}>{children}</As>
  );
}

export function PanelHead({
  title,
  meta,
  tone = "neutral",
  right,
}: {
  title: string;
  meta?: string | undefined;
  tone?: Tone;
  right?: ReactNode;
}) {
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-border bg-shell px-3 py-1.5">
      <span className={cn("h-3 w-px", toneBg(tone))} aria-hidden />
      <h2 className="label-xs text-muted-foreground">{title}</h2>
      {meta ? <span className="num text-[10px] text-faint">{meta}</span> : null}
      <div className="ml-auto flex items-center gap-2">{right}</div>
    </header>
  );
}

export function Metric({
  label,
  value,
  unit,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  unit?: string | undefined;
  tone?: Tone;
  hint?: string | undefined;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="label-xs">{label}</span>
      <span className={cn("num text-[15px] leading-none", toneText(tone))}>
        {value}
        {unit ? <span className="ml-1 text-[10px] text-faint">{unit}</span> : null}
      </span>
      {hint ? <span className="text-[10px] text-faint">{hint}</span> : null}
    </div>
  );
}

export function Row({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/60 py-1">
      <span className="label-xs">{label}</span>
      <span className={cn("num text-[11.5px]", toneText(tone))}>{value}</span>
    </div>
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  const map: Record<Tone, string> = {
    neutral: "border-border-strong text-muted-foreground",
    critical: "border-critical/70 text-critical",
    ok: "border-ok/60 text-ok",
    warning: "border-warning/60 text-warning",
    selected: "border-selected/60 text-selected",
    freight: "border-freight/50 text-freight",
    dim: "border-border text-faint",
  };
  return (
    <span
      className={cn(
        "num inline-flex items-center border px-1.5 py-[1px] text-[10px] tracking-wider uppercase",
        map[tone],
      )}
    >
      {children}
    </span>
  );
}

export const Btn = forwardRef<HTMLButtonElement, {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "warn" | "quiet";
  active?: boolean;
  disabled?: boolean;
  className?: string | undefined;
  title?: string | undefined;
}>(({ children, onClick, variant = "default", active, disabled, className, title }, ref) => {
  const styles: Record<string, string> = {
    default: "border-border-strong bg-panel-raised text-foreground hover:border-selected/70",
    primary: "border-ok/70 bg-ok/10 text-ok hover:bg-ok/20",
    danger: "border-critical/70 bg-critical/10 text-critical hover:bg-critical/20",
    warn: "border-warning/60 bg-warning/10 text-warning hover:bg-warning/20",
    quiet: "border-transparent bg-transparent text-muted-foreground hover:text-foreground",
  };
  return (
    <button
      ref={ref}
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "font-cond border px-2 py-[3px] text-[10.5px] tracking-[0.1em] uppercase transition-colors",
        styles[variant],
        active && "border-selected bg-selected/12 text-selected",
        disabled && "cursor-not-allowed opacity-40 hover:border-border-strong",
        className,
      )}
    >
      {children}
    </button>
  );
});
Btn.displayName = "Btn";

export function toneText(tone: Tone): string {
  switch (tone) {
    case "critical":
      return "text-critical";
    case "warning":
      return "text-warning";
    case "ok":
      return "text-ok";
    case "selected":
      return "text-selected";
    case "freight":
      return "text-freight";
    case "dim":
      return "text-faint";
    default:
      return "text-foreground";
  }
}

export function toneBg(tone: Tone): string {
  switch (tone) {
    case "critical":
      return "bg-critical";
    case "warning":
      return "bg-warning";
    case "ok":
      return "bg-ok";
    case "selected":
      return "bg-selected";
    case "freight":
      return "bg-freight";
    default:
      return "bg-border-strong";
  }
}
