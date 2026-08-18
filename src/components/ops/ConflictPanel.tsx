import { useTwin } from "@/twin/store";
import { conflictKindLabel, mmss } from "@/twin/format";
import { Btn, Panel, PanelHead, Tag } from "./primitives";

export function ConflictPanel({ className }: { className?: string }) {
  const { prediction, selectedConflict, selectConflict, focusMode, setFocusMode } = useTwin();
  const critical = prediction.conflicts.filter((c) => c.severity === "CRITICAL").length;

  return (
    <Panel className={className}>
      <PanelHead
        title="Predicted conflicts"
        meta={`${prediction.conflicts.length} in 15 min · ${critical} critical`}
        tone={critical > 0 ? "critical" : "ok"}
        right={
          <Btn active={focusMode} onClick={() => setFocusMode(!focusMode)}>
            Focus mode
          </Btn>
        }
      />
      {prediction.conflicts.length === 0 ? (
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          No resource contention predicted within the horizon.
        </p>
      ) : (
        <ul className="min-h-0 overflow-y-auto">
          {prediction.conflicts.map((c) => {
            const active = selectedConflict?.id === c.id;
            const tone = c.severity === "CRITICAL" ? "critical" : "warning";
            return (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => selectConflict(c.id)}
                  className={`w-full border-b border-border/60 px-3 py-2 text-left hover:bg-panel-raised ${
                    active ? "bg-panel-raised" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="num text-[11px] text-faint">{c.id.slice(0, 18)}</span>
                    <Tag tone={tone}>{c.severity}</Tag>
                    <span className="num ml-auto text-[11.5px]">T+{mmss(c.etaSec)}</span>
                  </div>
                  <div className="mt-1 text-[12px]">{conflictKindLabel(c.kind)}</div>
                  <div className="num mt-0.5 text-[11px] text-muted-foreground">
                    {c.trainA}
                    {c.trainB ? ` vs ${c.trainB}` : ""} · {c.resourceLabel}
                  </div>
                  <div className="num mt-0.5 text-[10.5px] text-faint">
                    separation {Math.round(c.separationSec)}s / required {c.requiredSeparationSec}s
                    · status {active ? "SELECTED" : "PREDICTED"}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
