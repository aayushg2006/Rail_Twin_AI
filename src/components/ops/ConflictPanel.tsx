import { useTwin } from "@/twin/store";
import { countdown } from "@/twin/format";
import { Panel, PanelHead, Tag } from "./primitives";

/**
 * Where the problem is, how bad it is, and how long you have (#7).
 *
 * The separation arithmetic ("separation 156s / required 180s · status
 * PREDICTED") is gone: it restated the same fact three times in units the
 * controller has to convert, and the raw conflict id (#8) told them nothing.
 */
export function ConflictPanel({
  className,
  openWhatIfOnSelect = false,
}: {
  className?: string;
  openWhatIfOnSelect?: boolean;
}) {
  const { conflicts, selectedConflict, selectConflict, openWhatIf, focusMode } = useTwin();
  const critical = conflicts.filter((c) => c.severity === "CRITICAL").length;
  const visible = focusMode && selectedConflict ? [selectedConflict] : conflicts;

  return (
    <Panel className={className}>
      <PanelHead
        title="Predicted conflicts"
        meta={
          conflicts.length
            ? `${conflicts.length} in 15 min · ${critical} critical`
            : "none in 15 min"
        }
        tone={critical > 0 ? "critical" : conflicts.length ? "warning" : "ok"}
      />
      {conflicts.length === 0 ? (
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          No contention predicted in the next 15 minutes.
        </p>
      ) : (
        <ul className="min-h-0 overflow-y-auto">
          {visible.map((c) => {
            const active = selectedConflict?.id === c.id;
            const tone = c.severity === "CRITICAL" ? "critical" : "warning";
            return (
              <li key={c.id}>
                <button
                  type="button"
                  aria-label={`${openWhatIfOnSelect ? "Open What-if for" : "Select"} ${c.resourceLabel}`}
                  onClick={() =>
                    openWhatIfOnSelect ? openWhatIf(c.id) : selectConflict(c.id)
                  }
                  className={`w-full border-b border-border/60 px-3 py-2 text-left hover:bg-panel-raised ${
                    active ? "bg-panel-raised" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Tag tone={tone}>{c.severity}</Tag>
                    <span className="num ml-auto text-[12px]">{countdown(c.etaSec)}</span>
                  </div>
                  <div className="mt-1 text-[12.5px]">{c.resourceLabel}</div>
                  <div className="num mt-0.5 text-[11px] text-muted-foreground">
                    {c.trainB ? `${trainNo(c.trainA)} and ${trainNo(c.trainB)}` : trainNo(c.trainA)}
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

/** Ids look like L-90632-308; a controller only ever says "90632". */
function trainNo(id: string): string {
  const parts = id.split("-");
  return parts.length > 1 ? (parts[1] as string) : id;
}
