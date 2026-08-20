import { Link } from "@tanstack/react-router";
import type { ScenarioId } from "@/domain/types";
import { useTwin } from "@/twin/store";
import { clockOf } from "@/twin/format";
import { Btn, Tag } from "./primitives";

const NAV = [
  ["/", "Live"],
  ["/conflict", "Conflict"],
  ["/decision", "What-if"],
  ["/log", "Record"],
] as const;

/**
 * Six presets, each with a distinct and independently verifiable mechanism
 * (#20). The Scenario tab is gone; Reset to base and Focus mode live here (#1,
 * #4) so there is exactly one of each in the whole console.
 */
const SCENARIOS: { id: ScenarioId; label: string }[] = [
  { id: "BASE", label: "Normal working" },
  { id: "PLATFORM_BLOCKED", label: "Platform out of use" },
  { id: "BRANCH_BLOCKED", label: "Diva branch obstructed" },
  { id: "SIGNAL_DEGRADED", label: "Signalling under caution" },
  { id: "FREIGHT_LATE", label: "Goods running late" },
  { id: "PEAK_SURGE", label: "Suburban bunching" },
];

export function TopBar() {
  const {
    bundle,
    connection,
    scenario,
    loadScenario,
    resetToBase,
    focusMode,
    setFocusMode,
    conflicts,
  } = useTwin();

  const clock = bundle ? clockOf(bundle.wallClockMs) : "--:--:--";
  const critical = conflicts.filter((c) => c.severity === "CRITICAL").length;
  const live = bundle?.liveData;

  const link =
    connection === "CONNECTED"
      ? { dot: "bg-ok", text: "Twin connected" }
      : connection === "RECONNECTING"
        ? { dot: "bg-warning", text: "Reconnecting…" }
        : { dot: "bg-critical", text: "Twin unreachable" };

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-shell px-3 py-1.5">
      <div className="flex items-baseline gap-2">
        <span className="font-cond text-[13px] tracking-[0.2em]">VASAI ROAD JN</span>
        <span className="label-xs">WR Mumbai Div</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="num text-[17px] leading-none">{clock}</span>
        <span className="label-xs">IST</span>
      </div>

      <Tag tone={critical > 0 ? "critical" : "ok"}>
        {critical > 0 ? `${critical} critical` : "No critical conflict"}
      </Tag>

      <nav className="flex items-center gap-px">
        {NAV.map(([to, label]) => (
          <Link
            key={to}
            to={to}
            activeOptions={{ exact: to === "/" }}
            activeProps={{ className: "border-selected bg-selected/12 text-selected" }}
            inactiveProps={{ className: "border-transparent text-muted-foreground" }}
            className="font-cond border px-2 py-[3px] text-[10.5px] tracking-[0.12em] uppercase hover:text-foreground"
          >
            {label}
          </Link>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {live?.enabled ? (
          <Tag tone={live.mode === "live" ? "ok" : "dim"}>
            {live.mode === "live" ? "RailRadar live" : "RailRadar replay"} ·{" "}
            {live.observedTrains} observed
          </Tag>
        ) : (
          <Tag tone="dim">No live feed</Tag>
        )}

        <Btn active={focusMode} onClick={() => setFocusMode(!focusMode)}>
          Focus mode
        </Btn>
        <Btn variant="warn" onClick={resetToBase}>
          Reset to base
        </Btn>

        <select
          value={scenario}
          onChange={(e) => loadScenario(e.target.value as ScenarioId)}
          className="font-cond border border-border bg-panel px-2 py-[3px] text-[10.5px] tracking-[0.08em] uppercase"
          aria-label="Scenario"
        >
          {SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>

        <span className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 ${link.dot}`} />
          <span className="label-xs">{link.text}</span>
        </span>
      </div>
    </header>
  );
}
