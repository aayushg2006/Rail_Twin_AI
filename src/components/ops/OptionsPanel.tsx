import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { signedMin } from "@/twin/format";
import { Btn, Panel, PanelHead, Tag } from "./primitives";

export function OptionsPanel({ className }: { className?: string }) {
  const { options, previewOption, setPreviewOptionId, recommendation, selectedConflict } = useTwin();

  return (
    <Panel className={className}>
      <PanelHead
        title="Resolution options — re-simulated"
        meta={selectedConflict ? `for ${selectedConflict.id.slice(0, 20)}` : "no conflict selected"}
        tone="selected"
        right={
          previewOption ? (
            <Btn variant="quiet" onClick={() => setPreviewOptionId(null)}>
              Clear preview
            </Btn>
          ) : null
        }
      />
      {options.length === 0 ? (
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          No option set — select a predicted conflict first.
        </p>
      ) : (
        <div className="min-h-0 overflow-auto">
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 bg-shell">
              <tr className="label-xs">
                {[
                  "Opt",
                  "Action",
                  "Conflict",
                  "Train delay",
                  "Network",
                  "Throughput",
                  "Infra",
                  "Safety",
                ].map((h) => (
                  <th key={h} className="border-b border-border px-2 py-1 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {options.map((o) => {
                const active = previewOption?.id === o.id;
                const isRec = recommendation?.optionId === o.id;
                return (
                  <tr
                    key={o.id}
                    onClick={() => setPreviewOptionId(o.id)}
                    className={`cursor-pointer border-b border-border/50 hover:bg-panel-raised ${
                      active ? "bg-panel-raised" : ""
                    } ${o.feasible ? "" : "opacity-50"}`}
                  >
                    <td className="num px-2 py-1 text-[11.5px]">
                      {o.letter}
                      {isRec ? <span className="ml-1 text-ok">★</span> : null}
                    </td>
                    <td className="px-2 py-1 text-[11.5px]">
                      {o.title}
                      {!o.feasible ? (
                        <span className="block text-[10px] text-faint">{o.infeasibleReason}</span>
                      ) : null}
                    </td>
                    <td className="px-2 py-1">
                      <Tag tone={o.conflictResolved ? "ok" : "critical"}>
                        {o.conflictResolved ? "Resolved" : `${o.residualConflicts} left`}
                      </Tag>
                    </td>
                    <td className="num px-2 py-1 text-[11px]">
                      {Object.entries(o.addedDelaySec)
                        .map(([id, d]) => `${fleetById[id]?.number ?? id} ${signedMin(d)}`)
                        .join(" · ") || "—"}
                    </td>
                    <td className="num px-2 py-1 text-[11.5px]">{signedMin(o.networkDelaySec)}</td>
                    <td className="num px-2 py-1 text-[11.5px]">
                      {o.throughputDelta >= 0 ? "+" : ""}
                      {o.throughputDelta}
                    </td>
                    <td className="num px-2 py-1 text-[11px]">{o.infrastructureChange}</td>
                    <td className="px-2 py-1">
                      <Tag tone={o.safety.passed ? "ok" : "critical"}>
                        {o.safety.passed ? "Valid" : "Fail"}
                      </Tag>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function SafetyPanel({ className }: { className?: string }) {
  const { previewOption } = useTwin();
  return (
    <Panel className={className}>
      <PanelHead
        title="Safety validation"
        meta={previewOption ? previewOption.title : "no option previewed"}
        tone={previewOption?.safety.passed ? "ok" : previewOption ? "critical" : "dim"}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        {!previewOption ? (
          <p className="text-[12px] text-muted-foreground">
            Select an option to run interlocking-style validation checks.
          </p>
        ) : (
          previewOption.safety.checks.map((c) => (
            <div key={c.id} className="flex items-start gap-2 border-b border-border/60 py-1.5">
              <span
                className="mt-1 h-1.5 w-1.5 shrink-0"
                style={{ background: c.passed ? "var(--ok)" : "var(--critical)" }}
              />
              <div>
                <div className="text-[11.5px]">{c.label}</div>
                <div className="num text-[10.5px] text-faint">{c.detail}</div>
              </div>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}
