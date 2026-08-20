import { useTwin } from "@/twin/store";
import { minutes } from "@/twin/format";
import { Btn, Panel, PanelHead, Tag } from "./primitives";

/**
 * The options table, in the controller's language (#16, #17).
 *
 * Gone: the Infra column and the raw conflict code in the header. Costs are now
 * stated in passenger-minutes - what the decision actually trades - and the best
 * option is starred and sorted first rather than left to be found.
 */
export function OptionsPanel({ className }: { className?: string }) {
  const { options, previewOption, setPreviewOptionId, recommendation, selectedConflict } =
    useTwin();

  return (
    <Panel className={className}>
      <PanelHead
        title="Options"
        meta={selectedConflict ? selectedConflict.resourceLabel : "no conflict selected"}
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
          Select a predicted conflict to compare the options for it.
        </p>
      ) : (
        <div className="min-h-0 overflow-auto">
          <table className="w-full border-collapse text-left">
            <thead className="sticky top-0 bg-shell">
              <tr className="label-xs">
                {["", "Action", "Result", "Passengers delayed", "Freight", "Safe"].map((h) => (
                  <th key={h} className="border-b border-border px-2 py-1 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {options.map((o) => {
                const active = previewOption?.id === o.id;
                const best = recommendation?.optionId === o.id;
                const usable = o.feasible && o.safety.passed;
                return (
                  <tr
                    key={o.id}
                    onClick={() => setPreviewOptionId(o.id)}
                    className={`cursor-pointer border-b border-border/50 hover:bg-panel-raised ${
                      active ? "bg-panel-raised" : ""
                    } ${usable ? "" : "opacity-55"}`}
                  >
                    <td className="num px-2 py-1.5 text-[11.5px]">
                      {best ? <span className="text-ok">★</span> : o.letter}
                    </td>
                    <td className="px-2 py-1.5 text-[12px]">
                      {o.responseClass === "CONTAINMENT" ? (
                        <Tag tone="warning">Containment</Tag>
                      ) : null}
                      {o.title}
                      {!usable && (
                        <span className="block text-[10px] text-faint">
                          {o.infeasibleReason ??
                            o.safety.checks.find((c) => !c.passed)?.detail ??
                            "Not permitted"}
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <Tag tone={o.conflictResolved ? "ok" : "critical"}>
                        {o.conflictResolved ? "Clears it" : `${o.residualConflicts} left`}
                      </Tag>
                    </td>
                    <td className="num px-2 py-1.5 text-[11.5px]">
                      {o.passengerMinutes > 0
                        ? `${o.passengerMinutes.toFixed(0)} passenger-min`
                        : "none"}
                    </td>
                    <td className="num px-2 py-1.5 text-[11.5px] text-freight">
                      {o.freightDelaySec > 0 ? minutes(o.freightDelaySec) : "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <Tag tone={o.safety.passed ? "ok" : "critical"}>
                        {o.safety.passed ? "Yes" : "No"}
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
  const { previewOption, recommendation } = useTwin();
  return (
    <Panel className={className}>
      <PanelHead
        title="Safety validation"
        meta={previewOption ? previewOption.title : "no option selected"}
        tone={previewOption?.safety.passed ? "ok" : previewOption ? "critical" : "dim"}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        {!previewOption ? (
          <p className="text-[12px] text-muted-foreground">
            {recommendation?.mode === "MONITORING"
              ? "No intervention is required."
              : "Select an option to run the interlocking-style checks."}
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
