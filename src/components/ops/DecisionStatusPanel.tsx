import { useTwin } from "@/twin/store";
import { Panel, PanelHead, Tag } from "./primitives";

export function DecisionStatusPanel() {
  const { decisions, acknowledgedDecision, decisionStatus } = useTwin();
  const latest = decisions[0];

  return (
    <Panel className="min-h-full">
      <PanelHead title="Decision status" meta="controller record" tone={latest ? "ok" : "dim"} />
      {!latest ? (
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          No controller decision has been made in this session.
        </p>
      ) : (
        <div className="px-3 py-3 text-[12px]">
          <div className="shrink-0 overflow-y-auto pr-1 lg:max-h-[58%]">
            <div className="flex items-start justify-between gap-3">
            <div>
              <div className="label-xs">Latest decision</div>
              <p className="mt-1">{latest.optionTitle}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{latest.conflictLabel}</p>
            </div>
            <Tag tone={latest.outcome === "REJECTED" ? "critical" : latest.outcome === "MODIFIED" ? "warning" : "ok"}>
              {acknowledgedDecision?.conflictId === latest.conflictId && decisionStatus?.status && decisionStatus.status !== "READY"
                ? decisionStatus.status
                : latest.outcome}
            </Tag>
            </div>
            <div className="border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
            <div className="label-xs">Action</div>
            <p className="mt-1">{latest.action.kind.replace(/_/g, " ")}</p>
              {latest.action.holdSec ? <p>Hold: {latest.action.holdSec}s</p> : null}
              {latest.action.speedKmh ? <p>Speed: {latest.action.speedKmh} km/h</p> : null}
              <p className={latest.delayAvoidedSec < 0 ? "text-critical" : latest.delayAvoidedSec > 0 ? "text-ok" : "text-faint"}>
                Delay avoided: {(latest.delayAvoidedSec / 60).toFixed(1)} min
              </p>
            </div>
            {latest.description ? (
              <div className="border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
              <div className="label-xs">Decision description</div>
              <p className="mt-1 leading-relaxed">{latest.description}</p>
              </div>
            ) : null}
            {latest.expectedOutcome ? (
              <div className="border-t border-border/60 pt-2 text-[11px] text-muted-foreground">
              <div className="label-xs">Expected outcome</div>
              <p className="mt-1 leading-relaxed">{latest.expectedOutcome}</p>
              </div>
            ) : null}
            {latest.note ? <p className="border-t border-border/60 pt-2 text-[11px] text-faint">Note: {latest.note}</p> : null}
          </div>
          <div className="mt-3 border-t border-border/60 pt-2">
            <div className="label-xs">Decision log</div>
            <div className="mt-2 pr-1">
              <div className="space-y-1">
                {decisions.map((decision, index) => (
                  <details key={decision.id} open={index === 0} className="border-b border-border/50 pb-1 text-[11px]">
                    <summary className="cursor-pointer list-none py-1 hover:text-foreground">
                      <div className="flex items-start justify-between gap-2">
                        <span className="num text-faint">{decision.wallClock}</span>
                        <span className={decision.outcome === "ACCEPTED" ? "text-ok" : decision.outcome === "REJECTED" ? "text-critical" : "text-warning"}>
                          {decision.outcome}
                        </span>
                      </div>
                      <p className="mt-1 text-foreground">{decision.optionTitle}</p>
                    </summary>
                    <div className="pb-1 text-faint">
                      <p>{decision.conflictLabel}</p>
                      {decision.description ? <p className="mt-1 leading-relaxed text-muted-foreground">{decision.description}</p> : null}
                      {decision.expectedOutcome ? <p className="mt-1 leading-relaxed text-muted-foreground">Expected: {decision.expectedOutcome}</p> : null}
                      <p className={decision.delayAvoidedSec < 0 ? "mt-1 text-critical" : decision.delayAvoidedSec > 0 ? "mt-1 text-ok" : "mt-1 text-faint"}>
                        Delay avoided: {(decision.delayAvoidedSec / 60).toFixed(1)} min
                      </p>
                      {decision.note ? <p className="mt-1">Note: {decision.note}</p> : null}
                    </div>
                  </details>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}
