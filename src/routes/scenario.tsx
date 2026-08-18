import { createFileRoute } from "@tanstack/react-router";
import { TwinMap } from "@/components/twin/TwinMap";
import { ScenarioPanel } from "@/components/ops/ScenarioPanel";
import { ConflictPanel } from "@/components/ops/ConflictPanel";
import { Timeline } from "@/components/ops/Timeline";
import { KpiBar } from "@/components/ops/KpiBar";

export const Route = createFileRoute("/scenario")({
  head: () => ({
    meta: [
      { title: "Scenario Lab — Vasai Road Junction Digital Twin" },
      {
        name: "description",
        content:
          "Inject freight delay, platform unavailability, signal failure or peak traffic at Vasai Road Jn and watch the twin recompute conflicts.",
      },
      { property: "og:title", content: "Scenario Lab — Vasai Road Junction Digital Twin" },
      {
        property: "og:description",
        content: "Disruption injection and live recompute for Vasai Road Junction.",
      },
    ],
  }),
  component: ScenarioScreen,
});

function ScenarioScreen() {
  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">Scenario laboratory</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-[380px_minmax(0,1fr)_320px] gap-px bg-border">
        <ScenarioPanel />
        <div className="min-h-[480px] bg-map">
          <TwinMap />
        </div>
        <ConflictPanel />
      </div>
      <KpiBar />
    </main>
  );
}
