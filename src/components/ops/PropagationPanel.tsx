import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { signedMin } from "@/twin/format";
import { Panel, PanelHead, Tag } from "./primitives";

const CAUSE_LABEL: Record<string, string> = {
  EVENT: "External event",
  JUNCTION_OCCUPANCY: "Junction occupancy",
  BLOCK_OCCUPANCY: "Block occupancy",
  PLATFORM_OCCUPANCY: "Platform occupancy",
  HEADWAY: "Headway",
};

const BUCKET_LABEL: Record<string, string> = {
  base_schedule: "Entry / schedule",
  block_wait: "Block wait",
  junction_wait: "Junction wait",
  platform_wait: "Platform wait",
  headway_wait: "Headway wait",
  event: "Event",
  hold: "Controller hold",
  regulation: "Speed regulation",
  dwell: "Extra dwell",
};

function trainNo(id: string): string {
  return fleetById[id]?.number ?? id;
}

/**
 * Delay-propagation view. The causal chain and per-cause delay breakdown are
 * computed by the backend twin as delays actually propagate — not scripted.
 */
export function PropagationPanel({ className }: { className?: string }) {
  const { causalChain, delayBuckets, selectedTrainId } = useTwin();

  // Trains carrying propagated (non-entry) delay, worst first.
  const propagated = Object.entries(delayBuckets)
    .map(([id, b]) => {
      const causes = Object.entries(b).filter(
        ([k, v]) => k !== "total" && k !== "base_schedule" && Math.abs(v as number) > 1,
      ) as [string, number][];
      const nonBase = causes.reduce((s, [, v]) => s + v, 0);
      return { id, buckets: b, causes, nonBase };
    })
    .filter((t) => t.nonBase > 1)
    .sort((a, b) => b.nonBase - a.nonBase);

  const chain = [...causalChain].reverse().slice(0, 12);
  const focus = selectedTrainId;

  return (
    <Panel className={className}>
      <PanelHead
        title="Delay propagation — causal chain"
        meta={`${causalChain.length} links · computed`}
        tone={chain.length ? "warning" : "dim"}
      />
      <div className="min-h-0 overflow-y-auto px-3 py-2">
        {chain.length === 0 && propagated.length === 0 ? (
          <p className="text-[12px] text-muted-foreground">
            No propagated delay yet. Inject a disruption or apply a hold — the twin
            computes the downstream cascade here.
          </p>
        ) : null}

        {chain.length > 0 ? (
          <>
            <div className="label-xs">Causal chain (most recent)</div>
            <ol className="mt-1 space-y-1">
              {chain.map((l, i) => {
                const hot = focus && l.affected_train === focus;
                return (
                  <li
                    key={`${l.timestamp}-${i}`}
                    className={`flex items-center gap-2 border-b border-border/50 py-1 ${
                      hot ? "bg-panel-raised" : ""
                    }`}
                  >
                    <Tag tone="freight">{CAUSE_LABEL[l.cause_type] ?? l.cause_type}</Tag>
                    <span className="num text-[11px]">
                      {l.cause_entity.length > 16 ? l.cause_entity.slice(0, 16) : l.cause_entity}
                    </span>
                    <span className="text-faint">→</span>
                    <span className="num text-[11.5px]">{trainNo(l.affected_train)}</span>
                    <span className="num text-[10.5px] text-faint">@ {l.resource}</span>
                    <span className="num ml-auto text-[11.5px] text-warning">
                      {signedMin(l.added_delay_seconds)}
                    </span>
                  </li>
                );
              })}
            </ol>
          </>
        ) : null}

        {propagated.length > 0 ? (
          <>
            <div className="label-xs mt-3">Delay breakdown by cause</div>
            <div className="mt-1 space-y-2">
              {propagated.slice(0, 6).map((t) => (
                <div
                  key={t.id}
                  className={`border border-border/60 p-1.5 ${
                    focus === t.id ? "border-selected/60" : ""
                  }`}
                >
                  <div className="flex items-baseline gap-2">
                    <span className="num text-[11.5px]">{trainNo(t.id)}</span>
                    <span className="num ml-auto text-[11px] text-warning">
                      {signedMin(t.buckets.total)} total
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    {t.causes
                      .sort((a, b) => b[1] - a[1])
                      .map(([k, v]) => (
                        <span key={k} className="num text-[10.5px] text-muted-foreground">
                          {BUCKET_LABEL[k] ?? k}{" "}
                          <span className="text-warning">{signedMin(v)}</span>
                        </span>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : null}
      </div>
    </Panel>
  );
}
