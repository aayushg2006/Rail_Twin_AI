import { useState } from "react";
import { useTwin } from "@/twin/store";
import { dataPack, fleet, scenarios } from "@/twin/scenario";
import { resourceById } from "@/twin/topology";
import type { ScenarioEvent } from "@/domain/types";
import { Btn, Panel, PanelHead, Row, Tag } from "./primitives";

export function ScenarioPanel({ className }: { className?: string }) {
  const { scenario, loadScenario, loadCustomScenario, sim, prediction } = useTwin();
  const [kind, setKind] = useState<ScenarioEvent["kind"]>("TRAIN_DELAY");
  const [trainId, setTrainId] = useState(fleet[0]?.id ?? "");
  const [resourceId, setResourceId] = useState("JB");
  const [value, setValue] = useState(120);
  const [atSec, setAtSec] = useState(0);
  const [durationSec, setDurationSec] = useState(0);
  const [events, setEvents] = useState<ScenarioEvent[]>([]);
  const [jsonDraft, setJsonDraft] = useState("");

  const infrastructureEvent = kind === "BLOCK" || kind === "PLATFORM";
  const targetId = kind === "HEADWAY" ? "NETWORK" : infrastructureEvent ? resourceId : trainId;

  function currentEvent(): ScenarioEvent {
    return { id: `EV-${Date.now()}`, kind, targetId, value, atSec, durationSec: durationSec || undefined };
  }

  function addEvent() {
    setEvents((list) => [...list, currentEvent()]);
  }

  function injectCustom() {
    const nextEvents = events.length ? events : [currentEvent()];
    loadCustomScenario({
      id: `CUSTOM-${Date.now()}`,
      label: "Controller-built scenario",
      description: `${kind} on ${targetId}`,
      events: nextEvents,
    });
  }

  function loadJsonScenario() {
    try {
      const parsed = JSON.parse(jsonDraft) as { events?: ScenarioEvent[] };
      if (!Array.isArray(parsed.events) || parsed.events.length > 16) throw new Error("events must be an array of at most 16 items");
      setEvents(parsed.events);
    } catch (error) {
      window.alert(`Invalid scenario JSON: ${error instanceof Error ? error.message : "unknown error"}`);
    }
  }

  return (
    <Panel className={className}>
      <PanelHead
        title="Scenario injection"
        meta={`active · ${scenario}`}
        tone="warning"
        right={<Btn onClick={() => loadScenario("BASE")}>Reset to base</Btn>}
      />
      <div className="border-b border-border px-3 py-2">
        <p className="text-[10px] text-faint">Data pack: {dataPack.label} · snapshot {dataPack.snapshotDate}</p>
        <p className="text-[10px] text-freight">Freight: synthetic-demo · FOIS feed not connected</p>
      </div>
      <div className="min-h-0 overflow-y-auto">
        <ul className="divide-y divide-border/60">
          {scenarios.map((s) => (
            <li key={s.id} className="flex items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[12.5px]">{s.label}</span>
                  {scenario === s.id ? <Tag tone="selected">Active</Tag> : null}
                </div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">{s.description}</p>
              </div>
              <Btn
                variant={scenario === s.id ? "primary" : "default"}
                onClick={() => loadScenario(s.id)}
              >
                Inject
              </Btn>
            </li>
          ))}
        </ul>
      </div>
      <div className="border-t border-border px-3 py-2">
        <div className="label-xs mb-2">Custom scenario builder</div>
        <div className="grid grid-cols-2 gap-1.5">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as ScenarioEvent["kind"])}
            className="border border-border bg-map px-2 py-1 text-[11px]"
          >
            <option value="TRAIN_DELAY">Train delay</option>
            <option value="SPEED_RESTRICTION">Speed restriction</option>
            <option value="HOLD">Controller hold</option>
            <option value="BREAKDOWN">Breakdown</option>
            <option value="BLOCK">Block outage</option>
            <option value="PLATFORM">Platform outage</option>
            <option value="HEADWAY">Headway change</option>
          </select>
          {infrastructureEvent ? (
            <select value={resourceId} onChange={(e) => setResourceId(e.target.value)} className="border border-border bg-map px-2 py-1 text-[11px]">
              {Object.values(resourceById).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
          ) : kind === "HEADWAY" ? (
            <div className="border border-border bg-map px-2 py-1 text-[11px] text-faint">Whole network</div>
          ) : (
            <select value={trainId} onChange={(e) => setTrainId(e.target.value)} className="border border-border bg-map px-2 py-1 text-[11px]">
              {fleet.map((f) => <option key={f.id} value={f.id}>{f.number} · {f.type}</option>)}
            </select>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <input
            type="number"
            value={value}
            min={kind === "HEADWAY" ? 0.5 : 0}
            max={kind === "HEADWAY" ? 4 : kind === "SPEED_RESTRICTION" || kind === "BREAKDOWN" ? 120 : 1800}
            step={kind === "HEADWAY" ? 0.1 : 30}
            onChange={(e) => setValue(Number(e.target.value))}
            className="w-20 border border-border bg-map px-2 py-1 text-[11px]"
          />
          <span className="flex-1 text-[10px] text-faint">{kind === "HEADWAY" ? "multiplier" : kind === "SPEED_RESTRICTION" || kind === "BREAKDOWN" ? "km/h" : "seconds"}</span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <input type="number" min={0} value={atSec} onChange={(e) => setAtSec(Number(e.target.value))} placeholder="start sec" className="border border-border bg-map px-2 py-1 text-[11px]" />
          <input type="number" min={0} value={durationSec} onChange={(e) => setDurationSec(Number(e.target.value))} placeholder="duration sec" className="border border-border bg-map px-2 py-1 text-[11px]" />
        </div>
        <div className="mt-2 flex gap-2">
          <Btn onClick={addEvent}>Add event</Btn>
          <Btn variant="warn" onClick={injectCustom}>Simulate {events.length ? `(${events.length})` : "event"}</Btn>
          {events.length ? <Btn variant="quiet" onClick={() => setEvents([])}>Clear</Btn> : null}
        </div>
        {events.length ? <ul className="mt-2 space-y-1 text-[10px] text-muted-foreground">{events.map((event, i) => <li key={event.id} className="flex justify-between border border-border px-2 py-1"><span>{i + 1}. {event.kind} · {event.targetId} · T+{event.atSec ?? 0}s</span><button onClick={() => setEvents((list) => list.filter((_, index) => index !== i))}>×</button></li>)}</ul> : null}
        <textarea value={jsonDraft} onChange={(e) => setJsonDraft(e.target.value)} placeholder='Advanced JSON: {"events":[...]}' className="mt-2 h-16 w-full border border-border bg-map px-2 py-1 text-[10px]" />
        <Btn onClick={loadJsonScenario}>Load JSON events</Btn>
        <p className="mt-1 text-[10px] text-faint">Events are validated by the backend and marked as controller-built.</p>
      </div>
      <div className="border-t border-border px-3 py-2">
        <Row label="Blocked resources" value={
          sim.blockedResources.length
            ? sim.blockedResources.map((r) => resourceById[r]?.label ?? r).join(" · ")
            : "NONE"
        } tone={sim.blockedResources.length ? "critical" : "ok"} />
        <Row label="Headway multiplier" value={`${sim.headwayMultiplier.toFixed(2)}×`} />
        <Row label="Routes unavailable" value={sim.unavailableRoutes.join(", ") || "NONE"} />
        <Row
          label="Conflicts after injection"
          value={String(prediction.conflicts.length)}
          tone={prediction.conflicts.length ? "warning" : "ok"}
        />
      </div>
    </Panel>
  );
}
