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
    <main className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden">
      <h1 className="sr-only">Scenario laboratory</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-px bg-border lg:grid-cols-[380px_minmax(0,1fr)_320px]">
        <div className="flex min-h-0 flex-col max-lg:min-h-[60vh]">
          <ScenarioPanel />
        </div>
        <div className="min-h-[300px] bg-map lg:min-h-[480px]">
          <TwinMap />
        </div>
        <div className="flex min-h-0 flex-col max-lg:min-h-[40vh]">
          <ConflictPanel />
        </div>
      </div>
      <KpiBar />
    </main>
  );
}
