import { createFileRoute } from "@tanstack/react-router";
import { TwinMap } from "@/components/twin/TwinMap";
import { OptionsPanel, SafetyPanel } from "@/components/ops/OptionsPanel";
import { DecisionPanel } from "@/components/ops/DecisionPanel";
import { Timeline } from "@/components/ops/Timeline";
import { KpiBar } from "@/components/ops/KpiBar";

export const Route = createFileRoute("/decision")({
  head: () => ({
    meta: [
      { title: "What-if & Decision — Vasai Road Junction Digital Twin" },
      {
        name: "description",
        content:
          "Re-simulate hold, speed regulation and rerouting options at Vasai Road Jn, validate safety, then accept, modify or reject.",
      },
      { property: "og:title", content: "What-if & Decision — Vasai Road Junction Digital Twin" },
      {
        property: "og:description",
        content: "Compare re-simulated resolution options and record the controller decision.",
      },
    ],
  }),
  component: DecisionScreen,
});

function DecisionScreen() {
  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">What-if simulation and decision</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px] gap-px bg-border">
        <div className="grid min-h-0 grid-rows-[minmax(300px,1fr)_auto] gap-px bg-border">
          <div className="bg-map">
            <TwinMap />
          </div>
          <OptionsPanel className="max-h-[38vh]" />
        </div>
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-px bg-border">
          <DecisionPanel />
          <SafetyPanel className="max-h-[38vh]" />
        </div>
      </div>
      <KpiBar />
    </main>
  );
}
