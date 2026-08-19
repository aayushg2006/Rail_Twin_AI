import { useState } from "react";
import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { mmss, signedMin } from "@/twin/format";
import type { FailureMetrics } from "@/domain/types";
import { Btn, Panel, PanelHead, Row, Tag } from "./primitives";

export function DecisionPanel({ className }: { className?: string }) {
  const { recommendation, options, previewOption, selectedConflict, prediction, acknowledgedDecision, decide, setPreviewOptionId, decisionStatus, globalPlan, modelStatus } =
    useTwin();
  const [modify, setModify] = useState(false);
  const [holdSec, setHoldSec] = useState(120);
  const [speedKmh, setSpeedKmh] = useState(40);
  const [note, setNote] = useState("");

  const { mlByConflict } = useTwin();
  const rec = recommendation?.optionId ? options.find((o) => o.id === recommendation.optionId) : null;
  const target = previewOption ?? rec;
  const ml = selectedConflict ? mlByConflict[selectedConflict.id] : undefined;

  if (acknowledgedDecision && !selectedConflict) {
    const stillPredicted = prediction.conflicts.some((c) => c.id === acknowledgedDecision.conflictId);
    const wasRejected = acknowledgedDecision.outcome === "REJECTED" || decisionStatus?.status === "REJECTED";
    return (
      <Panel className={className}>
        <PanelHead title="Decision" meta={wasRejected ? "DECISION CLOSED" : "LIVE ACTION ACCEPTED"} tone={wasRejected ? "dim" : "ok"} />
        <div className="px-3 py-4 text-[12px] text-muted-foreground">
          <p className={wasRejected ? "text-critical" : "text-ok"}>
            {wasRejected ? "Controller decision rejected." : acknowledgedDecision.outcome === "MODIFIED" ? "Modified action accepted." : "Action accepted."}
          </p>
          <p className="mt-1">{acknowledgedDecision.optionTitle}</p>
          <p className="mt-2 text-[11px] text-faint">
            {wasRejected
              ? "The What-if preview is closed. No controller action was applied."
              : "The What-if preview is closed. The action is now part of the live controller plan and remains subject to interlocking authority."}
          </p>
          {stillPredicted ? (
            <p className="mt-2 text-[11px] text-warning">
              The original conflict is still present in the current prediction; select it again to inspect a new What-if evaluation.
            </p>
          ) : (
            <p className="mt-2 text-[11px] text-ok">The original conflict is no longer present in the current prediction.</p>
          )}
          <ModelStatus modelStatus={modelStatus} />
        </div>
      </Panel>
    );
  }

  if (recommendation?.mode === "MONITORING") {
    return (
      <Panel className={className}>
        <PanelHead title="Recommendation" meta="MONITORING · no intervention required" tone="ok" />
        <div className="px-3 py-4 text-[12px] text-muted-foreground">
          <p>{recommendation.rationale}</p>
          <p className="mt-2 text-[11px] text-faint">{recommendation.expectedOutcome}</p>
          <ModelStatus modelStatus={modelStatus} />
          <FailureMetricsView metrics={recommendation.failureMetrics} />
        </div>
      </Panel>
    );
  }

  if (!selectedConflict || !recommendation || !target) {
    return (
      <Panel className={className}>
        <PanelHead title="Recommendation" meta="AI recommends · human decides" tone="dim" />
        <div className="px-3 py-4 text-[12px] text-muted-foreground">
          <p>{!selectedConflict ? "No conflict is selected; continue monitoring the current horizon." : recommendation?.rationale ?? "No safe action is currently available for this conflict."}</p>
          <ModelStatus modelStatus={modelStatus} />
          <FailureMetricsView metrics={recommendation?.failureMetrics} />
        </div>
      </Panel>
    );
  }

  const trainNo = fleetById[target.action.trainId]?.number ?? target.action.trainId;

  return (
    <Panel className={className}>
      <PanelHead
        title="Recommendation"
        meta="AI recommends · human decides"
        tone={recommendation.mode === "CONTAINMENT" ? "warning" : "selected"}
        right={<Tag tone={target.safety.passed ? "ok" : "critical"}>Option {target.letter}</Tag>}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        {recommendation.mode === "CONTAINMENT" ? (
          <div className="mb-2 border border-warning/60 bg-warning/5 px-2 py-1.5 text-[11px]">
            <div className="font-medium text-warning">Full resolution unavailable</div>
            <div className="mt-0.5 text-muted-foreground">This safe action protects the movement but does not claim the blocked resource or residual conflicts are resolved.</div>
          </div>
        ) : null}
        {globalPlan ? (
          <div className="mb-2 border border-border px-2 py-1 text-[10px]">
            Global plan: <span className="text-selected">{globalPlan.status}</span> · {globalPlan.clearedConflicts.length} cleared · {globalPlan.residualConflicts.length} residual
          </div>
        ) : null}
        {decisionStatus && decisionStatus.status !== "READY" ? (
          <p className={`mb-2 border px-2 py-1 text-[10.5px] ${decisionStatus.status === "STALE" || decisionStatus.status === "REJECTED" ? "border-critical/50 text-critical" : "border-ok/50 text-ok"}`}>
            {decisionStatus.status}: {decisionStatus.reason ?? "live decision status"}
          </p>
        ) : null}
        <div className="label-xs">What</div>
        <p className="mt-0.5 text-[12.5px]">{target.title}</p>

        <div className="label-xs mt-2">Why</div>
        <p className="mt-0.5 text-[12px] text-muted-foreground">{recommendation.rationale}</p>
        <p className="mt-1 text-[11px] text-faint">{recommendation.expectedOutcome}</p>
        <ModelStatus modelStatus={modelStatus} />
        <FailureMetricsView metrics={recommendation.failureMetrics} />

        {ml ? (
          <>
            <div className="label-xs mt-2">ML conflict assessment</div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <Tag tone={ml.value >= 0.5 ? "critical" : ml.value >= 0.25 ? "warning" : "ok"}>
                {(ml.value * 100).toFixed(0)}% conflict prob
              </Tag>
              <Tag tone={ml.status === "OK" ? "ok" : "dim"}>
                conf {(ml.confidence * 100).toFixed(0)}%
              </Tag>
              <span className="num text-[9.5px] text-faint">{ml.modelVersion}</span>
            </div>
            {ml.contributions.length ? (
              <ul className="mt-1 space-y-0.5">
                {ml.contributions.slice(0, 4).map((c) => (
                  <li key={c.feature} className="num text-[10.5px] text-muted-foreground">
                    <span className={c.contribution >= 0 ? "text-critical" : "text-ok"}>
                      {c.contribution >= 0 ? "▲" : "▼"}
                    </span>{" "}
                    {c.feature.replace(/_/g, " ")}
                    <span className="text-faint"> ({c.value})</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {ml.status !== "OK" ? (
              <p className="mt-1 text-[10px] text-faint">
                Low confidence — deterministic projection used; safety validation stays authoritative.
              </p>
            ) : null}
          </>
        ) : null}

        <div className="label-xs mt-2">Impact</div>
        <div className="mt-1">
          <Row label="Conflict outcome" value={target.conflictResolved ? "Cleared" : `${target.residualConflicts} residual`} tone={target.conflictResolved ? "ok" : "critical"} />
          <Row label="Network delay" value={signedMin(target.networkDelaySec)} />
          <Row label="Passenger delay" value={signedMin(target.passengerDelaySec)} />
          <Row label="Freight delay" value={signedMin(target.freightDelaySec)} tone="freight" />
          <Row label="Throughput delta" value={`${target.throughputDelta >= 0 ? "+" : ""}${target.throughputDelta}`} />
          <Row label="Time to conflict" value={`T+${mmss(selectedConflict.etaSec)}`} tone="warning" />
        </div>

        <div className="label-xs mt-2">Alternatives</div>
        <ul className="mt-0.5 space-y-0.5">
          {recommendation.alternatives.map((a) => (
            <li key={a} className="text-[11.5px] text-muted-foreground">
              · {a}
            </li>
          ))}
        </ul>

        {modify ? (
          <div className="mt-3 border border-border-strong bg-panel-raised p-2">
            <div className="label-xs">Modify parameters for {trainNo}</div>
            <label className="mt-2 flex items-center gap-2 text-[11px]">
              <span className="label-xs w-20">Hold</span>
              <input
                type="range"
                min={0}
                max={480}
                step={30}
                value={holdSec}
                onChange={(e) => setHoldSec(Number(e.target.value))}
                className="flex-1 accent-[var(--selected)]"
              />
              <span className="num w-12 text-right">{mmss(holdSec)}</span>
            </label>
            <label className="mt-1 flex items-center gap-2 text-[11px]">
              <span className="label-xs w-20">Regulate</span>
              <input
                type="range"
                min={15}
                max={90}
                step={5}
                value={speedKmh}
                onChange={(e) => setSpeedKmh(Number(e.target.value))}
                className="flex-1 accent-[var(--selected)]"
              />
              <span className="num w-12 text-right">{speedKmh}</span>
            </label>
            <div className="mt-2 flex gap-2">
              <Btn
                variant="warn"
                onClick={() => {
                  decide(target, "MODIFIED", note || `Modified: hold ${mmss(holdSec)}`, {
                    kind: "HOLD",
                    trainId: target.action.trainId,
                    holdSec,
                  });
                  setModify(false);
                }}
              >
                Apply hold
              </Btn>
              <Btn
                variant="warn"
                onClick={() => {
                  decide(target, "MODIFIED", note || `Modified: regulate to ${speedKmh} km/h`, {
                    kind: "SPEED_REGULATION",
                    trainId: target.action.trainId,
                    speedKmh,
                  });
                  setModify(false);
                }}
              >
                Apply speed
              </Btn>
              <Btn variant="quiet" onClick={() => setModify(false)}>
                Cancel
              </Btn>
            </div>
          </div>
        ) : null}

        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Controller note (recorded in the log)"
          className="mt-3 w-full border border-border bg-map px-2 py-1 text-[11.5px] outline-none focus:border-selected"
        />

        <div className="mt-2 flex flex-wrap gap-2">
          <Btn
            variant="primary"
            disabled={!target.feasible || !target.safety.passed}
            onClick={() => {
              decide(target, "ACCEPTED", note);
              setNote("");
              setPreviewOptionId(null);
            }}
          >
            Accept
          </Btn>
          <Btn variant="warn" onClick={() => setModify((m) => !m)}>
            Modify
          </Btn>
          <Btn
            variant="danger"
            onClick={() => {
              decide(target, "REJECTED", note || "Rejected by controller");
              setNote("");
            }}
          >
            Reject
          </Btn>
        </div>
        <p className="mt-2 text-[10.5px] text-faint">
          Advisory system. Movement authority stays with the interlocking and the section
          controller.
        </p>
      </div>
    </Panel>
  );
}

function ModelStatus({ modelStatus }: { modelStatus: { optimizer: { status: string; reason: string }; ml: { status: string; reason: string } } }) {
  return (
    <div className="mt-3 border-t border-border/60 pt-2 text-[10px] text-faint">
      <div className="label-xs">Model status</div>
      <div className="mt-1 grid grid-cols-2 gap-2">
        <span>Optimizer: <b className="text-foreground">{modelStatus.optimizer.status}</b></span>
        <span>ML: <b className="text-foreground">{modelStatus.ml.status}</b></span>
      </div>
      <div className="mt-1">{modelStatus.ml.reason}</div>
    </div>
  );
}

function FailureMetricsView({ metrics }: { metrics: FailureMetrics | undefined }) {
  if (!metrics) return null;
  return (
    <div className="mt-3 border-t border-border/60 pt-2 text-[10.5px]">
      <div className="label-xs">Failure metrics</div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-muted-foreground">
        <span>Candidates: {metrics.candidateCount}</span>
        <span>Safe: {metrics.safetyPassingCandidateCount}</span>
        <span>Clearing: {metrics.conflictClearingCandidateCount}</span>
        <span>Residual critical: {metrics.residualCriticalConflicts}</span>
        <span>Separation: {metrics.actualSeparationSec ?? "—"} / {metrics.requiredSeparationSec ?? "—"} s</span>
        <span>Deficit: {metrics.separationDeficitSec} s</span>
        <span>Network delay: {signedMin(metrics.networkDelaySec)}</span>
        <span>Weighted delay: {signedMin(metrics.weightedDelaySec)}</span>
      </div>
      <p className="mt-1 text-warning">Reason: {metrics.primaryFailureReason}</p>
      {metrics.failedSafetyChecks.length ? (
        <ul className="mt-1 space-y-0.5 text-critical">
          {metrics.failedSafetyChecks.map((check) => <li key={check.id}>{check.label}: {check.detail}</li>)}
        </ul>
      ) : null}
      {metrics.infeasibleReasons.length ? (
        <ul className="mt-1 space-y-0.5 text-critical">
          {metrics.infeasibleReasons.map((item) => <li key={item.candidate}>{item.candidate}: {item.reason}</li>)}
        </ul>
      ) : null}
    </div>
  );
}
