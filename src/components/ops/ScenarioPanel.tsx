import { useTwin } from "@/twin/store";
import { scenarios } from "@/twin/scenario";
import { resourceById } from "@/twin/topology";
import { Btn, Panel, PanelHead, Row, Tag } from "./primitives";

export function ScenarioPanel({ className }: { className?: string }) {
  const { scenario, loadScenario, sim, prediction } = useTwin();

  return (
    <Panel className={className}>
      <PanelHead
        title="Scenario injection"
        meta={`active · ${scenario}`}
        tone="warning"
        right={<Btn onClick={() => loadScenario("BASE")}>Reset to base</Btn>}
      />
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
