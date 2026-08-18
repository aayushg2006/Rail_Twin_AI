import { createFileRoute } from "@tanstack/react-router";
import { TwinMap } from "@/components/twin/TwinMap";
import { Timeline } from "@/components/ops/Timeline";
import { ConflictPanel } from "@/components/ops/ConflictPanel";
import { Inspector } from "@/components/ops/Inspector";
import { TrainList } from "@/components/ops/TrainList";
import { KpiBar } from "@/components/ops/KpiBar";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Live Console — Vasai Road Junction Digital Twin" },
      {
        name: "description",
        content:
          "Real-time schematic of Vasai Road Jn PF1–PF7, the Diva branch and goods chord, with 15-minute conflict prediction and decision support.",
      },
      { property: "og:title", content: "Live Console — Vasai Road Junction Digital Twin" },
      {
        property: "og:description",
        content:
          "Predictive junction control for Vasai Road: conflicts, options, safety validation.",
      },
    ],
  }),
  component: Console,
});

function Console() {
  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">Vasai Road Junction live operations console</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] gap-px bg-border">
        <div className="grid min-h-0 grid-rows-[minmax(320px,1fr)_auto] gap-px bg-border">
          <div className="bg-map">
            <TwinMap />
          </div>
          <TrainList className="max-h-[34vh]" />
        </div>
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-px bg-border">
          <ConflictPanel />
          <Inspector />
        </div>
      </div>
      <KpiBar />
    </main>
  );
}
