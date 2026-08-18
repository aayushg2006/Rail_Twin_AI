import { useTwin } from "@/twin/store";
import { mmss } from "@/twin/format";
import { toneText, type Tone } from "./primitives";

export function KpiBar() {
  const { kpis, baselineKpis } = useTwin();

  const items: { label: string; value: string; tone?: Tone; delta?: string | undefined }[] = [
    {
      label: "Active conflicts",
      value: String(kpis.activeConflicts),
      tone: kpis.activeConflicts > 0 ? "critical" : "ok",
    },
    { label: "Trains tracked", value: String(kpis.trainsTracked) },
    {
      label: "Total delay",
      value: `${(kpis.totalDelaySec / 60).toFixed(1)}′`,
      delta: baselineKpis
        ? `base ${(baselineKpis.totalDelaySec / 60).toFixed(1)}′`
        : undefined,
    },
    {
      label: "Earliness",
      value: `-${((kpis.earlinessSec ?? 0) / 60).toFixed(1)}′`,
      tone: "ok",
    },
    { label: "Avg delay", value: `${(kpis.averageDelaySec / 60).toFixed(1)}′` },
    {
      label: "Passenger delay",
      value: `${(kpis.passengerDelaySec / 60).toFixed(1)}′`,
    },
    {
      label: "Freight delay",
      value: `${(kpis.freightDelaySec / 60).toFixed(1)}′`,
      tone: "freight",
    },
    {
      label: "Throughput",
      value: kpis.throughputPerHour ? `${kpis.throughputPerHour}/h` : "NO DATA",
      tone: kpis.throughputPerHour ? "neutral" : "dim",
    },
    { label: "Platform use", value: `${Math.round(kpis.platformUtilisation * 100)}%` },
    {
      label: "On time",
      value: `${Math.round(kpis.onTimePercent)}%`,
      tone: kpis.onTimePercent >= 70 ? "ok" : "warning",
    },
    {
      label: "Recovery",
      value: kpis.recoveryTimeSec ? mmss(kpis.recoveryTimeSec) : "NO DATA",
      tone: kpis.recoveryTimeSec ? "warning" : "dim",
    },
    { label: "Missed", value: String(kpis.missedServices ?? 0), tone: (kpis.missedServices ?? 0) ? "warning" : "ok" },
  ];

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-1 border-t border-border bg-shell px-3 py-1.5">
      {items.map((it) => (
        <div key={it.label} className="flex items-baseline gap-2">
          <span className="label-xs">{it.label}</span>
          <span className={`num text-[12.5px] ${toneText(it.tone ?? "neutral")}`}>{it.value}</span>
          {it.delta ? <span className="num text-[10px] text-faint">{it.delta}</span> : null}
        </div>
      ))}
      <span className="label-xs ml-auto">
        Advisory only · interlocking remains authoritative
      </span>
    </div>
  );
}
