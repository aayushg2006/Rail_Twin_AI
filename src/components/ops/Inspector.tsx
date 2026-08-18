import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { conflictKindLabel, mmss, trainTypeLabel } from "@/twin/format";
import { occupiedPlatforms, projectFinish, routeFor } from "@/twin/engine";
import { platformById, resourceById, tracks } from "@/twin/topology";
import { Panel, PanelHead, Row, Tag } from "./primitives";

export function Inspector({ className }: { className?: string }) {
  const { selection, view, prediction, select } = useTwin();

  if (!selection) {
    return (
      <Panel className={className}>
        <PanelHead title="Inspector" meta="nothing selected" tone="dim" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          Select a train, platform, track or conflict on the schematic to inspect its state.
        </p>
      </Panel>
    );
  }

  if (selection.kind === "train") {
    const ts = view.trains[selection.id];
    const train = fleetById[selection.id];
    if (!ts || !train) return null;
    const route = routeFor(view, selection.id);
    const finish = projectFinish(view, selection.id);
    const conflicts = prediction.conflicts.filter(
      (c) => c.trainA === selection.id || c.trainB === selection.id,
    );
    const nextStop = route.stops[ts.nextStopIndex];
    return (
      <Panel className={className}>
        <PanelHead
          title={`Train ${train.number}`}
          meta={train.name}
          tone={conflicts.length ? "critical" : "neutral"}
          right={<Tag tone={train.type === "FREIGHT" ? "freight" : "neutral"}>{trainTypeLabel[train.type]}</Tag>}
        />
        <div className="min-h-0 overflow-y-auto px-3 pb-3">
          <Row label="Route" value={route.label} />
          <Row label="Origin → destination" value={`${train.origin} → ${train.destination}`} />
          <Row label="State" value={ts.state} />
          <Row label="Speed / nominal" value={`${Math.round(ts.speedKmh)} / ${Math.round(ts.nominalSpeedKmh)} km/h`} />
          <Row
            label="Delay"
            value={`+${mmss(ts.delaySec)}`}
            tone={ts.delaySec > 240 ? "critical" : ts.delaySec > 60 ? "warning" : "ok"}
          />
          <Row label="Priority" value={`P${train.priority}`} />
          <Row
            label="Next stop"
            value={nextStop ? `${platformById[nextStop.platformId]?.label ?? nextStop.platformId} · dwell ${nextStop.dwellSec}s` : "None remaining"}
          />
          <Row label="Hold remaining" value={ts.holdRemainingSec > 0 ? mmss(ts.holdRemainingSec) : "—"} />
          <Row label="Clears station area" value={finish === null ? "NO DATA" : `T+${mmss(finish)}`} />
          <div className="label-xs mt-3 mb-1">Involved conflicts</div>
          {conflicts.length === 0 ? (
            <p className="text-[11.5px] text-muted-foreground">None within the horizon.</p>
          ) : (
            conflicts.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => select({ kind: "conflict", id: c.id })}
                className="num block w-full border-b border-border/60 py-1 text-left text-[11px] hover:text-selected"
              >
                T+{mmss(c.etaSec)} · {conflictKindLabel(c.kind)} · {c.resourceLabel}
              </button>
            ))
          )}
        </div>
      </Panel>
    );
  }

  if (selection.kind === "platform") {
    const pf = platformById[selection.id];
    if (!pf) return null;
    const occ = occupiedPlatforms(view)[pf.id];
    const train = occ ? fleetById[occ] : undefined;
    const res = resourceById[pf.id];
    return (
      <Panel className={className}>
        <PanelHead title={pf.label} meta={pf.usage} tone={occ ? "warning" : "ok"} />
        <div className="px-3 pb-3">
          <Row label="Side" value={pf.side} />
          <Row label="Serves" value={pf.serves.join(", ")} />
          <Row label="Occupancy" value={train ? `${train.number} ${train.name}` : "CLEAR"} tone={occ ? "warning" : "ok"} />
          <Row label="Headway" value={res ? `${res.headwaySec}s` : "NO DATA"} />
          <Row label="Capacity" value={res ? String(res.capacity) : "1"} />
        </div>
      </Panel>
    );
  }

  if (selection.kind === "track") {
    const tr = tracks.find((t) => t.id === selection.id);
    if (!tr) return null;
    const users = Object.values(view.trains)
      .filter((ts) => !ts.finished && routeFor(view, ts.trainId).tracks.includes(tr.id))
      .map((ts) => fleetById[ts.trainId]!.number);
    return (
      <Panel className={className}>
        <PanelHead title={tr.name} meta={tr.id} />
        <div className="px-3 pb-3">
          <Row label="Line" value={tr.kind.replace(/_/g, " ")} />
          <Row label="Direction" value={tr.direction.replace(/_/g, " ")} />
          <Row label="Through line" value={tr.through ? "YES" : "NO"} />
          <Row label="Booked movements" value={users.length ? users.join(", ") : "NONE"} />
        </div>
      </Panel>
    );
  }

  const c = prediction.conflicts.find((x) => x.id === selection.id);
  if (!c) {
    return (
      <Panel className={className}>
        <PanelHead title="Conflict" meta="resolved / expired" tone="ok" />
        <p className="px-3 py-4 text-[12px] text-muted-foreground">
          This contention is no longer predicted within the horizon.
        </p>
      </Panel>
    );
  }
  return (
    <Panel className={className}>
      <PanelHead
        title={conflictKindLabel(c.kind)}
        meta={c.id}
        tone={c.severity === "CRITICAL" ? "critical" : "warning"}
        right={<Tag tone={c.severity === "CRITICAL" ? "critical" : "warning"}>{c.severity}</Tag>}
      />
      <div className="px-3 pb-3">
        <Row label="Contended resource" value={c.resourceLabel} />
        <Row label="Time to conflict" value={`T+${mmss(c.etaSec)}`} tone="warning" />
        <Row label="Train A" value={fleetById[c.trainA]?.number ?? c.trainA} />
        <Row label="Train B" value={c.trainB ? (fleetById[c.trainB]?.number ?? c.trainB) : "—"} />
        <Row label="Projected separation" value={`${Math.round(c.separationSec)}s`} tone="critical" />
        <Row label="Required separation" value={`${c.requiredSeparationSec}s`} />
      </div>
    </Panel>
  );
}
