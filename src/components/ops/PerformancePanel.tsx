import { useEffect, useMemo, useState } from "react";
import type { KPISet, TrendPoint } from "@/domain/types";
import { useTwin } from "@/twin/store";
import { clockShort, countdown, minutes } from "@/twin/format";
import { Metric, Panel, PanelHead, Tag } from "./primitives";

/**
 * RECORD — did the AI actually help? (#18)
 *
 * Every number here is measured. Two SHADOW TWINS run the same timetable and the
 * same disruptions on the same seed: one takes no controller action at all, the
 * other applies the traditional "higher class always goes first" rule. Delay
 * avoided is the live twin measured against them, continuously.
 *
 * The previous version reported the difference between the best option the
 * optimiser generated and the WORST one it generated for itself, which is not a
 * counterfactual, and every accepted decision recorded exactly 0.0 min avoided
 * because of a sign error in the frontend.
 */
export function PerformancePanel({ className }: { className?: string }) {
  const { bundle } = useTwin();
  const avoided = bundle?.delayAvoided;
  const baselines = bundle?.baselines;
  const trend = bundle?.trend ?? [];
  const plan = bundle?.globalPlan;
  const [benchmark, setBenchmark] = useState<BenchmarkReport | null>(null);

  useEffect(() => {
    const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
    const env = (import.meta as { env?: Record<string, string> }).env ?? {};
    const ws = env["VITE_BACKEND_WS_URL"] ?? `ws://${host}:8000/ws`;
    const http = ws.replace(/^ws/, "http").replace(/\/ws$/, "");
    fetch(`${http}/api/benchmark/latest`)
      .then((response) => response.json())
      .then((report: BenchmarkReport) => setBenchmark(report))
      .catch(() => setBenchmark({ status: "NOT_VALIDATED", validated: false,
        reason: "Benchmark service is unavailable." }));
  }, []);

  if (!bundle || !baselines) {
    return (
      <Panel className={className}>
        <PanelHead title="Performance" meta="waiting for the twin" tone="dim" />
      </Panel>
    );
  }

  const savedMin = (avoided?.vsDoNothingSec ?? 0) / 60;
  const savedVsRule = (avoided?.vsPriorityRuleSec ?? 0) / 60;

  return (
    <Panel className={className}>
      <PanelHead
        title="Performance"
        meta="AI vs no action vs priority rule — measured, not estimated"
        tone={savedMin > 0 ? "ok" : "neutral"}
        right={
          plan ? (
            <Tag tone={plan.solverStatus === "OPTIMAL" ? "ok" : "warning"}>
              {plan.solver} {plan.solverStatus.toLowerCase()}
              {plan.optimalityGap !== null && ` · gap ${(plan.optimalityGap * 100).toFixed(1)}%`}
            </Tag>
          ) : null
        }
      />

      <BenchmarkSummary report={benchmark} />

      <div className="grid grid-cols-2 gap-4 border-b border-border px-3 py-3 sm:grid-cols-4">
        <Metric label="Decisions applied" value={String(avoided?.decisionsApplied ?? 0)} />
        <Metric
          label="Rejected"
          value={String(avoided?.decisionsRejected ?? 0)}
          tone="dim"
        />
        <Metric
          label="Delay avoided"
          value={avoided?.settling ? "settling" : savedMin.toFixed(1)}
          unit={avoided?.settling ? "hold still being served" : "min vs no action"}
          tone={avoided?.settling ? "warning" : savedMin > 0.05 ? "ok" : "dim"}
        />
        <Metric
          label="Passenger-minutes saved"
          value={(avoided?.vsDoNothingPassengerMinutes ?? 0).toFixed(0)}
          tone={(avoided?.vsDoNothingPassengerMinutes ?? 0) > 0 ? "ok" : "dim"}
        />
      </div>

      <TrendChart trend={trend} />

      <ComparisonTable ai={baselines.ai} doNothing={baselines.doNothing} rule={baselines.priorityRule} />

      <p className="border-t border-border px-3 py-2 text-[10.5px] text-faint">
        Both baselines are full simulations on the same seed and the same disruptions;
        neither ever receives a controller decision. Positive numbers mean the AI-assisted
        twin is ahead.{" "}
        {avoided?.settling
          ? `A hold costs its time the moment it is issued and only repays it when the conflict it prevents would have bitten — ${Math.round((avoided.holdInFlightSec ?? 0))}s of hold is still being served, so the comparison has not settled yet.`
          : ""} {savedVsRule >= 0 ? "" : "A negative figure against the priority rule means the rigid rule happened to do better in this window — it is reported as measured."}
      </p>
    </Panel>
  );
}

interface BenchmarkSide {
  totalDelayMinutes: number;
  averageDelayMinutes: number;
  conflicts: number;
  throughputPerHour: number;
  platformUtilisationPercent: number;
}

interface BenchmarkReport {
  status: string;
  validated: boolean;
  reason?: string;
  aggregate?: {
    runs: number;
    ai: BenchmarkSide;
    fifo: BenchmarkSide;
    improvements: Record<string, number>;
  };
}

function BenchmarkSummary({ report }: { report: BenchmarkReport | null }) {
  if (!report?.aggregate) {
    return (
      <div className="border-b border-border px-3 py-2 text-[11px] text-muted-foreground">
        <span className="mr-2 text-critical">NOT VALIDATED</span>
        {report?.reason ?? "No signed 100-run release report is available yet."}
      </div>
    );
  }
  const { aggregate } = report;
  const rows = [
    ["Total delay", aggregate.fifo.totalDelayMinutes, aggregate.ai.totalDelayMinutes,
      aggregate.improvements.totalDelayReductionPercent, "min"],
    ["Conflicts", aggregate.fifo.conflicts, aggregate.ai.conflicts,
      aggregate.improvements.conflictReductionPercent, ""],
    ["Average delay", aggregate.fifo.averageDelayMinutes, aggregate.ai.averageDelayMinutes,
      aggregate.improvements.averageDelayReductionPercent, "min"],
    ["Throughput", aggregate.fifo.throughputPerHour, aggregate.ai.throughputPerHour,
      aggregate.improvements.throughputIncreasePercent, "trains/hr"],
    ["Platform utilisation", aggregate.fifo.platformUtilisationPercent,
      aggregate.ai.platformUtilisationPercent,
      aggregate.improvements.platformUtilisationIncreasePercent, "%"],
  ] as const;
  return (
    <div className="border-b border-border">
      <div className="flex items-center gap-3 bg-shell px-3 py-1.5">
        <span className="label-xs">Signed release benchmark</span>
        <span className="num text-[10px] text-faint">{aggregate.runs}/100 paired runs</span>
        <span className={`ml-auto label-xs ${report.validated ? "text-ok" : "text-critical"}`}>
          {report.validated ? "VALIDATED" : "NOT VALIDATED"}
        </span>
      </div>
      <div className="grid grid-cols-[1.3fr_repeat(3,1fr)] text-[11px]">
        {(["Metric", "FIFO", "Rail-Twin", "Measured change"] as const).map((heading) => (
          <div key={heading} className="border-t border-r border-border px-3 py-1 label-xs">{heading}</div>
        ))}
        {rows.flatMap(([label, fifo, ai, improvement, unit]) => [
          <div key={`${label}-label`} className="border-t border-r border-border px-3 py-1.5">{label}</div>,
          <div key={`${label}-fifo`} className="num border-t border-r border-border px-3 py-1.5">
            {fifo.toFixed(1)}{unit === "%" ? "%" : unit ? ` ${unit}` : ""}
          </div>,
          <div key={`${label}-ai`} className="num border-t border-r border-border px-3 py-1.5">
            {ai.toFixed(1)}{unit === "%" ? "%" : unit ? ` ${unit}` : ""}
          </div>,
          <div key={`${label}-imp`} className={`num border-t border-border px-3 py-1.5 ${
            improvement >= 0 ? "text-ok" : "text-critical"}`}>
            {improvement >= 0 ? "+" : ""}{improvement.toFixed(1)}%
          </div>,
        ])}
      </div>
    </div>
  );
}

/** Total lateness over time, three tracks. Pure SVG — no chart library, no
 *  invented smoothing, one point per shadow refresh. */
function TrendChart({ trend }: { trend: TrendPoint[] }) {
  const W = 720;
  const H = 150;
  const PAD = { l: 44, r: 12, t: 12, b: 20 };

  const series = useMemo(() => {
    if (trend.length < 2) return null;
    const max = Math.max(
      1,
      ...trend.flatMap((p) => [p.ai, p.doNothing, p.priorityRule]),
    );
    const x = (i: number) =>
      PAD.l + (i / (trend.length - 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => H - PAD.b - (v / max) * (H - PAD.t - PAD.b);
    const line = (pick: (p: TrendPoint) => number) =>
      trend.map((p, i) => `${x(i).toFixed(1)},${y(pick(p)).toFixed(1)}`).join(" ");
    return {
      max,
      ai: line((p) => p.ai),
      doNothing: line((p) => p.doNothing),
      rule: line((p) => p.priorityRule),
      first: trend[0],
      last: trend[trend.length - 1],
    };
  }, [trend]);

  if (!series) {
    return (
      <div className="border-b border-border px-3 py-6 text-center text-[11.5px] text-muted-foreground">
        Collecting measurements — the comparison appears once the shadow twins have
        run for a few seconds.
      </div>
    );
  }

  return (
    <div className="border-b border-border px-3 py-2">
      <div className="mb-1 flex items-center gap-4">
        <span className="label-xs">Total lateness across the section</span>
        <Key colour="var(--ok)" label="AI-assisted" />
        <Key colour="var(--critical)" label="No action" />
        <Key colour="var(--warning)" label="Priority rule" />
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-[150px] w-full" role="img"
           aria-label="Total lateness over time, AI versus baselines">
        <line x1={PAD.l} y1={H - PAD.b} x2={W - PAD.r} y2={H - PAD.b}
              stroke="var(--border)" strokeWidth={1} />
        <line x1={PAD.l} y1={PAD.t} x2={PAD.l} y2={H - PAD.b}
              stroke="var(--border)" strokeWidth={1} />
        <text x={4} y={PAD.t + 8} fontSize={9} fontFamily="var(--font-mono)" fill="var(--faint)">
          {(series.max / 60).toFixed(0)}m
        </text>
        <text x={4} y={H - PAD.b} fontSize={9} fontFamily="var(--font-mono)" fill="var(--faint)">
          0
        </text>
        <polyline points={series.doNothing} fill="none" stroke="var(--critical)" strokeWidth={1.6} />
        <polyline points={series.rule} fill="none" stroke="var(--warning)" strokeWidth={1.6}
                  strokeDasharray="5 4" />
        <polyline points={series.ai} fill="none" stroke="var(--ok)" strokeWidth={2.2} />
      </svg>
    </div>
  );
}

function Key({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="h-[2px] w-4" style={{ background: colour }} />
      <span className="label-xs">{label}</span>
    </span>
  );
}

/**
 * Metric order as requested (#19): passengers, freight, total, average, active
 * conflicts, on time.
 */
function ComparisonTable({
  ai,
  doNothing,
  rule,
}: {
  ai: KPISet;
  doNothing: KPISet;
  rule: KPISet;
}) {
  const rows: [string, (k: KPISet) => string, (k: KPISet) => number][] = [
    ["Passenger delay", (k) => minutes(k.passengerDelaySec), (k) => k.passengerDelaySec],
    ["Freight delay", (k) => minutes(k.freightDelaySec), (k) => k.freightDelaySec],
    ["Total lateness", (k) => minutes(k.totalLatenessSec), (k) => k.totalLatenessSec],
    ["Average lateness", (k) => minutes(k.averageLatenessSec), (k) => k.averageLatenessSec],
    ["Active conflicts", (k) => String(k.activeConflicts), (k) => k.activeConflicts],
    [
      "On time",
      (k) => (k.onTimePercent === null ? "not yet measured" : `${k.onTimePercent.toFixed(0)}%`),
      (k) => -(k.onTimePercent ?? 0),
    ],
  ];

  return (
    <div className="min-h-0 overflow-auto">
      <table className="w-full border-collapse text-left">
        <thead className="sticky top-0 bg-shell">
          <tr className="label-xs">
            <th className="border-b border-border px-3 py-1 font-medium">Metric</th>
            <th className="border-b border-border px-3 py-1 font-medium">AI-assisted</th>
            <th className="border-b border-border px-3 py-1 font-medium">No action</th>
            <th className="border-b border-border px-3 py-1 font-medium">Priority rule</th>
            <th className="border-b border-border px-3 py-1 font-medium">Best</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, fmt, score]) => {
            const scores: [string, number][] = [
              ["AI", score(ai)],
              ["None", score(doNothing)],
              ["Rule", score(rule)],
            ];
            const best = [...scores].sort((a, b) => a[1] - b[1])[0]?.[0] ?? "AI";
            return (
              <tr key={label} className="border-b border-border/50">
                <td className="px-3 py-1.5 text-[12px]">{label}</td>
                <td className={`num px-3 py-1.5 text-[11.5px] ${best === "AI" ? "text-ok" : ""}`}>
                  {fmt(ai)}
                </td>
                <td className="num px-3 py-1.5 text-[11.5px] text-muted-foreground">
                  {fmt(doNothing)}
                </td>
                <td className="num px-3 py-1.5 text-[11.5px] text-muted-foreground">
                  {fmt(rule)}
                </td>
                <td className="px-3 py-1.5">
                  <Tag tone={best === "AI" ? "ok" : "dim"}>
                    {best === "AI" ? "AI" : best === "None" ? "No action" : "Priority rule"}
                  </Tag>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The audit trail. Rejections are first-class records, with the reason (#18). */
export function DecisionLogTable({ className }: { className?: string }) {
  const { decisions, bundle } = useTwin();
  const epoch = bundle?.simState.epochStartMs ?? 0;

  return (
    <Panel className={className}>
      <PanelHead
        title="Decision record"
        meta={`${decisions.length} recorded · append-only`}
        tone={decisions.length ? "neutral" : "dim"}
      />
      <div className="min-h-0 overflow-auto">
        {decisions.length === 0 ? (
          <p className="px-3 py-4 text-[12px] text-muted-foreground">
            No controller decisions yet. Accept, modify or reject a recommendation and it
            is recorded here — including what the twin measured before and after.
          </p>
        ) : (
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 bg-shell">
              <tr className="label-xs">
                {["Time", "Where", "Command", "Outcome", "Effect", "Note"].map((h) => (
                  <th key={h} className="border-b border-border px-2 py-1 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...decisions].reverse().map((d) => {
                const rejected = d.outcome === "REJECTED";
                const delta =
                  d.latenessBeforeSec !== undefined && d.latenessAfterSec !== undefined
                    ? d.latenessAfterSec - d.latenessBeforeSec
                    : null;
                return (
                  <tr key={d.id} className="border-b border-border/50 align-top">
                    <td className="num px-2 py-1.5 text-[11px]">
                      {clockShort(epoch + d.simTimeSec * 1000)}
                    </td>
                    <td className="px-2 py-1.5 text-[11.5px]">
                      {d.where || "—"}
                      {d.trains.length > 0 && (
                        <span className="block text-[10px] text-faint">
                          {d.trains.map((t) => t.split("-")[1] ?? t).join(" · ")}
                        </span>
                      )}
                    </td>
                    <td className="num px-2 py-1.5 text-[11px]">
                      {commandOf(d.action)}
                    </td>
                    <td className="px-2 py-1.5">
                      <Tag tone={rejected ? "critical" : d.outcome === "MODIFIED" ? "warning" : "ok"}>
                        {d.outcome}
                      </Tag>
                      {d.reason && (
                        <span className="block text-[10px] text-critical">{d.reason}</span>
                      )}
                    </td>
                    <td className="num px-2 py-1.5 text-[11px]">
                      {rejected ? (
                        <span className="text-faint">not applied</span>
                      ) : d.projectedDelaySavingSec !== undefined ? (
                        <span className="text-ok">
                          {minutes(d.projectedDelaySavingSec)} predicted saving
                          {d.conflictCleared ? " · cleared" : ""}
                        </span>
                      ) : delta === null ? (
                        <span className="text-faint">—</span>
                      ) : (
                        <span className={delta <= 0 ? "text-ok" : "text-warning"}>
                          {delta <= 0 ? "" : "+"}
                          {(delta / 60).toFixed(1)} min
                          {d.conflictCleared ? " · cleared" : ""}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-[11px] text-muted-foreground">
                      {d.note || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function commandOf(a: { kind: string; holdSec?: number; speedKmh?: number; platformId?: string }) {
  if (a.kind === "HOLD") return `Hold ${Math.round(a.holdSec ?? 0)}s`;
  if (a.kind === "SPEED_REGULATION") return `Regulate ${Math.round(a.speedKmh ?? 0)} km/h`;
  if (a.kind === "PLATFORM_REASSIGNMENT") return `Re-platform ${a.platformId ?? ""}`.trim();
  return a.kind.replace(/_/g, " ").toLowerCase();
}
