import { Link } from "@tanstack/react-router";
import { useTwin } from "@/twin/store";
import { clockOf, mmss } from "@/twin/format";
import { scenarios } from "@/twin/scenario";
import { Tag } from "./primitives";

const NAV = [
  ["/", "Live"],
  ["/conflict", "Conflict"],
  ["/decision", "What-if"],
  ["/scenario", "Scenario"],
  ["/log", "Record"],
] as const;

export function TopBar() {
  const { sim, prediction, scenario, loadScenario, horizonOffset, connection, clockMode, setClockMode } = useTwin();
  const clock = clockOf(sim.epochStartMs + sim.simTimeSec * 1000);
  const critical = prediction.conflicts.filter((c) => c.severity === "CRITICAL").length;
  const link =
    connection === "CONNECTED"
      ? { dot: "bg-ok", text: "Backend twin · live" }
      : connection === "RECONNECTING"
        ? { dot: "bg-warning", text: "Reconnecting to twin..." }
      : connection === "SIMULATED"
        ? { dot: "bg-warning", text: "In-browser sim · offline" }
        : { dot: "bg-faint", text: "Connecting to twin…" };

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b border-border bg-shell px-3 py-1.5">
      <div className="flex items-baseline gap-2">
        <span className="font-cond text-[13px] tracking-[0.2em]">VASAI ROAD JN</span>
        <span className="label-xs">Digital twin · WR Mumbai Div · Diva branch</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="num text-[17px] leading-none">{clock}</span>
        <span className="label-xs">IST</span>
        <span className="num text-[10px] text-faint">SIM T+{mmss(sim.simTimeSec)}</span>
        <select
          value={clockMode}
          onChange={(e) => setClockMode(e.target.value as "LIVE" | "DEMO")}
          className="font-cond border border-border bg-panel px-1 py-[2px] text-[9px] uppercase"
          aria-label="Clock mode"
        >
          <option value="LIVE">Live clock</option>
          <option value="DEMO">Demo snapshot</option>
        </select>
      </div>

      <Tag tone={horizonOffset > 0 ? "selected" : "neutral"}>
        {horizonOffset > 0 ? `Projected T+${mmss(horizonOffset)}` : "Live"}
      </Tag>
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

      <div className="ml-auto flex items-center gap-3">
        <span className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 ${link.dot}`} />
          <span className="label-xs">{link.text}</span>
        </span>
        <select
          value={scenario}
          onChange={(e) => loadScenario(e.target.value as never)}
          className="font-cond border border-border bg-panel px-2 py-[3px] text-[10.5px] tracking-[0.08em] uppercase"
          aria-label="Scenario"
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}
