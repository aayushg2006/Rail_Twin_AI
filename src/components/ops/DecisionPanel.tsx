import { useState } from "react";
import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { mmss, signedMin } from "@/twin/format";
import { Btn, Panel, PanelHead, Row, Tag } from "./primitives";

export function DecisionPanel({ className }: { className?: string }) {
  const { recommendation, options, previewOption, selectedConflict, decide, setPreviewOptionId } =
    useTwin();
  const [modify, setModify] = useState(false);
  const [holdSec, setHoldSec] = useState(120);
  const [speedKmh, setSpeedKmh] = useState(40);
  const [note, setNote] = useState("");

  const { mlByConflict } = useTwin();
  const rec = recommendation ? options.find((o) => o.id === recommendation.optionId) : null;
  const target = previewOption ?? rec;
  const ml = selectedConflict ? mlByConflict[selectedConflict.id] : undefined;

  if (!selectedConflict || !recommendation || !target) {
    return (
      <Panel className={className}>
        <PanelHead title="Recommendation" meta="AI recommends · human decides" tone="dim" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          No active recommendation. The twin issues advice only when a conflict is predicted.
        </p>
      </Panel>
    );
  }

  const trainNo = fleetById[target.action.trainId]?.number ?? target.action.trainId;

  return (
    <Panel className={className}>
      <PanelHead
        title="Recommendation"
        meta="AI recommends · human decides"
        tone="selected"
        right={<Tag tone={target.safety.passed ? "ok" : "critical"}>Option {target.letter}</Tag>}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        <div className="label-xs">What</div>
        <p className="mt-0.5 text-[12.5px]">{target.title}</p>

        <div className="label-xs mt-2">Why</div>
        <p className="mt-0.5 text-[12px] text-muted-foreground">{recommendation.rationale}</p>

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
