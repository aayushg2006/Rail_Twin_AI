import { useEffect, useState } from "react";
import { useTwin } from "@/twin/store";
import { countdown, minutes } from "@/twin/format";
import { Btn, Panel, PanelHead, Row, Tag } from "./primitives";

/**
 * What the AI proposes, why, and what it costs.
 *
 * Removed: model status (#10), failure metrics (#11), the raw conflict code
 * (#8) and the "Advisory system…" footer (#14). What remains is What / Why /
 * Impact (#12) plus the controller's response.
 */
export function DecisionPanel({ className }: { className?: string }) {
  const {
    recommendation,
    options,
    previewOption,
    selectedConflict,
    decide,
    setPreviewOptionId,
    bundle,
  } = useTwin();

  const [modify, setModify] = useState(false);
  const [holdSec, setHoldSec] = useState(120);
  const [speedKmh, setSpeedKmh] = useState(40);
  const [note, setNote] = useState("");

  const recommended = recommendation?.optionId
    ? options.find((o) => o.id === recommendation.optionId)
    : null;
  const target = previewOption ?? recommended ?? null;

  /**
   * Modify starts from the action being proposed, not from arbitrary defaults
   * (#13). The old panel opened at hold=120s / speed=40 km/h whatever the
   * conflict was, so "modify" silently discarded the whole calculation.
   */
  useEffect(() => {
    if (!target) return;
    setHoldSec(Math.round(target.action.holdSec ?? holdFallback(target.action.trainId, options)));
    setSpeedKmh(Math.round(target.action.speedKmh ?? 40));
  }, [target?.id, options]);

  if (!selectedConflict || !recommendation || !target) {
    return (
      <Panel className={className}>
        <PanelHead title="Recommendation" meta="AI proposes · controller decides" tone="dim" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          {recommendation?.rationale ??
            "Select a predicted conflict to see the options for it."}
        </p>
      </Panel>
    );
  }

  const containment = recommendation.mode === "CONTAINMENT";
  const ml = bundle?.mlByConflict?.[selectedConflict.id];

  /** The modified action, re-costed against the option it started from. */
  const modifiedHold = { kind: "HOLD" as const, trainId: target.action.trainId, holdSec };
  const modifiedSpeed = {
    kind: "SPEED_REGULATION" as const,
    trainId: target.action.trainId,
    speedKmh,
  };
  const enough = holdSec >= (target.action.holdSec ?? 0);

  return (
    <Panel className={className}>
      <PanelHead
        title="Recommendation"
        meta={selectedConflict.resourceLabel}
        tone={containment ? "warning" : "selected"}
        right={<Tag tone={target.safety.passed ? "ok" : "critical"}>Option {target.letter}</Tag>}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        {containment && (
          <div className="mb-2 border border-warning/60 bg-warning/5 px-2 py-1.5 text-[11px]">
            <div className="font-medium text-warning">Cannot be fully cleared</div>
            <div className="mt-0.5 text-muted-foreground">
              This protects the movement but does not claim the conflict is resolved.
            </div>
          </div>
        )}

        <div className="label-xs">What</div>
        <p className="mt-0.5 text-[13px]">{target.title}</p>

        <div className="label-xs mt-3">Why</div>
        <p className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">
          {recommendation.rationale}
        </p>
        {recommendation.costBreakdown && (
          <p className="mt-1 text-[11px] text-faint">
            {costSentence(target.passengerMinutes, target.freightDelaySec)}
          </p>
        )}

        {ml && (
          <p className="mt-2 text-[11px] text-faint">
            Conflict likelihood {(ml.value * 100).toFixed(0)}% (model confidence{" "}
            {(ml.confidence * 100).toFixed(0)}%
            {ml.status !== "OK" ? ", low — projection is authoritative" : ""}).
          </p>
        )}

        <div className="label-xs mt-3">Impact</div>
        <div className="mt-1">
          <Row
            label="Conflict"
            value={target.conflictResolved ? "Cleared" : `${target.residualConflicts} still open`}
            tone={target.conflictResolved ? "ok" : "critical"}
          />
          <Row
            label="Passengers delayed"
            value={`${target.passengerMinutes.toFixed(0)} passenger-min`}
            tone={target.passengerMinutes > 500 ? "warning" : "neutral"}
          />
          <Row label="Freight delayed" value={minutes(target.freightDelaySec)} tone="freight" />
          <Row label="Whole section" value={minutes(target.networkDelaySec)} />
          <Row label="Happens" value={countdown(selectedConflict.etaSec)} tone="warning" />
        </div>

        {recommendation.alternatives.length > 0 && (
          <>
            <div className="label-xs mt-3">Instead of</div>
            <ul className="mt-0.5 space-y-0.5">
              {recommendation.alternatives.map((a) => (
                <li key={a.title} className="text-[11.5px] text-muted-foreground">
                  · {a.title} — {a.passengerMinutes.toFixed(0)} passenger-min
                </li>
              ))}
            </ul>
          </>
        )}

        {modify && (
          <div className="mt-3 border border-border-strong bg-panel-raised p-2">
            <div className="label-xs">Adjust the command</div>
            <label className="mt-2 flex items-center gap-2 text-[11px]">
              <span className="label-xs w-16">Hold</span>
              <input
                type="range"
                min={0}
                max={Math.max(600, Math.round((target.action.holdSec ?? 0) * 2))}
                step={10}
                value={holdSec}
                onChange={(e) => setHoldSec(Number(e.target.value))}
                className="flex-1 accent-[var(--selected)]"
              />
              <span className="num w-14 text-right">{holdSec}s</span>
            </label>
            {!enough && (
              <p className="mt-1 text-[10.5px] text-warning">
                Shorter than the {Math.round(target.action.holdSec ?? 0)}s the twin calculated —
                the conflict may not clear. The backend re-validates before applying.
              </p>
            )}
            <label className="mt-2 flex items-center gap-2 text-[11px]">
              <span className="label-xs w-16">Regulate</span>
              <input
                type="range"
                min={15}
                max={Math.round(target.action.speedKmh ? target.action.speedKmh * 2 : 100)}
                step={5}
                value={speedKmh}
                onChange={(e) => setSpeedKmh(Number(e.target.value))}
                className="flex-1 accent-[var(--selected)]"
              />
              <span className="num w-14 text-right">{speedKmh} km/h</span>
            </label>
            <div className="mt-2 flex gap-2">
              <Btn
                variant="warn"
                onClick={() => {
                  decide(target, "MODIFIED", note || `Hold ${holdSec}s`, modifiedHold);
                  setModify(false);
                  setNote("");
                }}
              >
                Apply hold
              </Btn>
              <Btn
                variant="warn"
                onClick={() => {
                  decide(target, "MODIFIED", note || `Regulate to ${speedKmh} km/h`, modifiedSpeed);
                  setModify(false);
                  setNote("");
                }}
              >
                Apply speed
              </Btn>
              <Btn variant="quiet" onClick={() => setModify(false)}>
                Cancel
              </Btn>
            </div>
          </div>
        )}

        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Controller note (kept in the record)"
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
      </div>
    </Panel>
  );
}

/** The price of the command, the way a controller would say it. */
function costSentence(passengerMinutes: number, freightDelaySec: number): string {
  const parts: string[] = [];
  if (passengerMinutes > 1) parts.push(`${passengerMinutes.toFixed(0)} passenger-minutes`);
  if (freightDelaySec > 30) parts.push(`${(freightDelaySec / 60).toFixed(1)} min of freight time`);
  if (parts.length === 0) return "Costs nothing measurable elsewhere on the section.";
  return `Costs ${parts.join(" and ")}.`;
}

function holdFallback(trainId: string, options: { action: { trainId: string; holdSec?: number } }[]) {
  const match = options.find((o) => o.action.trainId === trainId && o.action.holdSec);
  return match?.action.holdSec ?? 120;
}
