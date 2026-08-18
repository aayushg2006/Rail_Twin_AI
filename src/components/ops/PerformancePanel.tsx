import { useTwin } from "@/twin/store";
import { Metric, Panel, PanelHead, Row } from "./primitives";

export function PerformancePanel({ className }: { className?: string }) {
  const { kpis, baselineKpis, decisions, delayAvoidedSec } = useTwin();
  const accepted = decisions.filter((d) => d.outcome !== "REJECTED");
  // Prefer the backend counterfactual (delay a naive controller would have
  // added vs. the AI's choices); fall back to the baseline−current difference.
  const saved =
    delayAvoidedSec ??
    (baselineKpis ? Math.max(0, baselineKpis.totalDelaySec - kpis.totalDelaySec) : 0);

  const rows: [string, number, number][] = baselineKpis
    ? [
        ["Total delay (min)", baselineKpis.totalDelaySec / 60, kpis.totalDelaySec / 60],
        ["Average delay (min)", baselineKpis.averageDelaySec / 60, kpis.averageDelaySec / 60],
        ["Passenger delay (min)", baselineKpis.passengerDelaySec / 60, kpis.passengerDelaySec / 60],
        ["Freight delay (min)", baselineKpis.freightDelaySec / 60, kpis.freightDelaySec / 60],
        ["Active conflicts", baselineKpis.activeConflicts, kpis.activeConflicts],
        ["On time (%)", baselineKpis.onTimePercent, kpis.onTimePercent],
      ]
    : [];

  return (
    <Panel className={className}>
      <PanelHead title="Performance — baseline vs current" meta={`${decisions.length} decisions recorded`} />
      <div className="grid grid-cols-2 gap-4 border-b border-border px-3 py-3 sm:grid-cols-4">
        <Metric label="Decisions applied" value={String(accepted.length)} />
        <Metric label="Rejected" value={String(decisions.length - accepted.length)} tone="dim" />
        <Metric label="Delay avoided" value={`${(saved / 60).toFixed(1)}`} unit="min" tone="ok" />
        <Metric
          label="Conflicts now"
          value={String(kpis.activeConflicts)}
          tone={kpis.activeConflicts ? "critical" : "ok"}
        />
      </div>
      <div className="px-3 py-2">
        {rows.length === 0 ? (
          <p className="text-[12px] text-muted-foreground">Baseline capture pending.</p>
        ) : (
          rows.map(([label, base, now]) => {
            const delta = now - base;
            return (
              <Row
                key={label}
                label={label}
                value={`${base.toFixed(1)} → ${now.toFixed(1)}  (${delta >= 0 ? "+" : ""}${delta.toFixed(1)})`}
                tone={delta > 0.05 ? "critical" : delta < -0.05 ? "ok" : "neutral"}
              />
            );
          })
        )}
      </div>
    </Panel>
  );
}

export function DecisionLogTable({ className }: { className?: string }) {
  const { decisions } = useTwin();
  return (
    <Panel className={className}>
      <PanelHead title="Decision record" meta="append-only audit trail" />
      <div className="min-h-0 overflow-auto">
        {decisions.length === 0 ? (
          <p className="px-3 py-4 text-[12px] text-muted-foreground">
            No controller decisions recorded in this session.
          </p>
        ) : (
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 bg-shell">
              <tr className="label-xs">
                {["Time", "Conflict", "Option", "Action", "Outcome", "Network", "Note"].map((h) => (
                  <th key={h} className="border-b border-border px-2 py-1 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {decisions.map((d) => (
                <tr key={d.id} className="border-b border-border/50">
                  <td className="num px-2 py-1 text-[11px]">{d.wallClock}</td>
                  <td className="num px-2 py-1 text-[11px]">{d.conflictLabel}</td>
                  <td className="px-2 py-1 text-[11.5px]">{d.optionTitle}</td>
                  <td className="num px-2 py-1 text-[11px]">
                    {d.action.kind}
                    {d.action.holdSec ? ` ${d.action.holdSec}s` : ""}
                    {d.action.speedKmh ? ` ${d.action.speedKmh}km/h` : ""}
                  </td>
                  <td
                    className={`num px-2 py-1 text-[11px] ${
                      d.outcome === "ACCEPTED"
                        ? "text-ok"
                        : d.outcome === "REJECTED"
                          ? "text-critical"
                          : "text-warning"
                    }`}
                  >
                    {d.outcome}
                  </td>
                  <td className="num px-2 py-1 text-[11px]">
                    {(d.networkDelaySec / 60).toFixed(1)}′
                  </td>
                  <td className="px-2 py-1 text-[11px] text-muted-foreground">{d.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}
