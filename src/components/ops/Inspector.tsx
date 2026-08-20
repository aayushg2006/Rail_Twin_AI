import { useTwin } from "@/twin/store";
import { countdown, lateness, minutes } from "@/twin/format";
import { CLASS_LABEL } from "@/twin/projection";
import { Panel, PanelHead, Row, Tag } from "./primitives";

/** Contextual detail for whatever is selected on the schematic. */
export function Inspector({ className }: { className?: string }) {
  const { selection, bundle, conflicts, select, network } = useTwin();

  if (!selection || !bundle) {
    return (
      <Panel className={className}>
        <PanelHead title="Inspector" meta="nothing selected" tone="dim" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          Select a train, a platform or a conflict on the schematic.
        </p>
      </Panel>
    );
  }

  if (selection.kind === "train") {
    const t = bundle.simState.trains[selection.id];
    if (!t) return null;
    const mine = conflicts.filter((c) => c.trainA === t.trainId || c.trainB === t.trainId);
    const observation = bundle.liveData.observations.find((o) => o.number === t.number);
    const delays = t.delayBreakdown;
    const causes = ([
      ["Late entering the section", delays.entry],
      ["Waiting at a signal", delays.block_wait + delays.junction_wait + delays.headway_wait],
      ["Waiting for a platform", delays.platform_wait],
      ["Extended station stop", delays.dwell],
      ["Held by the controller", delays.hold],
      ["Running under regulation", delays.regulation],
    ] as [string, number][]).filter(([, v]) => v > 1);

    return (
      <Panel className={className}>
        <PanelHead
          title={`Train ${t.number}`}
          meta={t.name}
          tone={mine.length ? "critical" : "neutral"}
          right={
            <Tag tone={t.category === "FREIGHT" ? "freight" : "neutral"}>
              {CLASS_LABEL[t.serviceClass] ?? t.category}
            </Tag>
          }
        />
        <div className="min-h-0 overflow-y-auto px-3 pb-3">
          <Row label="Working" value={`${t.origin} → ${t.destination}`} />
          <Row label="Through the junction" value={`${t.arrivalCorridor} → ${t.departureCorridor}`} />
          <Row label="Platform" value={t.platformId ?? "runs through"} />
          <Row label="Speed" value={`${Math.round(t.speedKmh)} of ${Math.round(t.lineSpeedKmh)} km/h`} />
          <Row
            label="Running"
            value={lateness(t.latenessSec)}
            tone={t.latenessSec > 300 ? "critical" : t.latenessSec > 60 ? "warning" : "ok"}
          />
          <Row label="On board" value={t.typicalLoad ? `${t.typicalLoad.toLocaleString()} passengers` : "goods"} />
          <Row label="State" value={t.state} />
          {t.holdRemainingSec > 0 && (
            <Row label="Hold remaining" value={`${Math.round(t.holdRemainingSec)}s`} tone="critical" />
          )}

          {causes.length > 0 && (
            <>
              <div className="label-xs mt-3 mb-1">Where the time went</div>
              {causes.map(([label, v]) => (
                <Row key={label} label={label} value={minutes(v)} />
              ))}
            </>
          )}

          {observation && (
            <p className="mt-3 border-t border-border/60 pt-2 text-[10.5px] text-faint">
              Last live observation {Math.round(observation.latenessSec / 60)} min late at{" "}
              {observation.lastStation || "an unreported point"} ({observation.source}).
            </p>
          )}
          {t.provenance === "synthetic" && (
            <p className="mt-2 text-[10.5px] text-faint">
              Goods path — generated, not observed. No public live feed exists for freight.
            </p>
          )}

          <div className="label-xs mt-3 mb-1">Conflicts involving this train</div>
          {mine.length === 0 ? (
            <p className="text-[11.5px] text-muted-foreground">None in the next 15 minutes.</p>
          ) : (
            mine.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => select({ kind: "conflict", id: c.id })}
                className="block w-full border-b border-border/60 py-1 text-left text-[11.5px] hover:text-selected"
              >
                {c.resourceLabel} · {countdown(c.etaSec)}
              </button>
            ))
          )}
        </div>
      </Panel>
    );
  }

  if (selection.kind === "platform") {
    const pf = network?.platforms.find((p) => p.id === selection.id);
    if (!pf) return null;
    const blocked = bundle.simState.blockedResources.includes(pf.id);
    const occupant = Object.values(bundle.simState.trains).find(
      (t) => t.admitted && !t.finished && t.platformId === pf.id && t.state === "DWELL",
    );
    const res = network?.resources.find((r) => r.id === pf.id);
    return (
      <Panel className={className}>
        <PanelHead
          title={pf.label}
          meta={pf.usage}
          tone={blocked ? "critical" : occupant ? "warning" : "ok"}
        />
        <div className="px-3 pb-3">
          <Row label="Side" value={pf.side} />
          <Row label="Length" value={`${pf.lengthM.toFixed(0)} m`} />
          <Row label="Serves" value={pf.serves.join(", ")} />
          <Row
            label="Now"
            value={blocked ? "OUT OF USE" : occupant ? `${occupant.number} ${occupant.name}` : "clear"}
            tone={blocked ? "critical" : occupant ? "warning" : "ok"}
          />
          <Row label="Minimum interval" value={res ? `${res.headwaySec.toFixed(0)}s` : "—"} />
        </div>
      </Panel>
    );
  }

  const c = conflicts.find((x) => x.id === selection.id);
  if (!c) {
    return (
      <Panel className={className}>
        <PanelHead title="Conflict" meta="resolved or expired" tone="ok" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          This contention is no longer predicted.
        </p>
      </Panel>
    );
  }
  return (
    <Panel className={className}>
      <PanelHead
        title={c.resourceLabel}
        meta={c.resourceKind.toLowerCase()}
        tone={c.severity === "CRITICAL" ? "critical" : "warning"}
        right={<Tag tone={c.severity === "CRITICAL" ? "critical" : "warning"}>{c.severity}</Tag>}
      />
      <div className="px-3 pb-3">
        <Row label="Happens" value={countdown(c.etaSec)} tone="warning" />
        <Row label="First movement" value={short(c.trainA)} />
        <Row label="Second movement" value={c.trainB ? short(c.trainB) : "—"} />
        <Row
          label="What goes wrong"
          value={
            c.severity === "CRITICAL"
              ? "The second train would be stopped at the signal"
              : "Too close for the required interval; the train loses time"
          }
        />
      </div>
    </Panel>
  );
}

/** Ids look like L-90632-308; a controller only ever says "90632". */
function short(id: string): string {
  const parts = id.split("-");
  return parts.length > 1 ? (parts[1] as string) : id;
}
