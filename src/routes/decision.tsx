import { createFileRoute } from "@tanstack/react-router";
import { TwinMap } from "@/components/twin/TwinMap";
import { Timeline } from "@/components/ops/Timeline";
import { WhatIfModal } from "@/components/ops/WhatIfModal";
import { ConflictPanel } from "@/components/ops/ConflictPanel";

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
    <main className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden">
      <h1 className="sr-only">What-if simulation and decision</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-px bg-border lg:grid-cols-[minmax(0,1fr)_clamp(300px,24vw,420px)]">
        <div className="relative min-h-0 bg-map">
          <TwinMap />
          <WhatIfModal />
        </div>
        <div className="min-h-0 max-h-full overflow-y-scroll overscroll-contain bg-border max-lg:max-h-[42vh]">
          <ConflictPanel />
        </div>
      </div>
    </main>
  );
}
