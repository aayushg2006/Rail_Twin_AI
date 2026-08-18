import { createFileRoute } from "@tanstack/react-router";
import { TwinMap } from "@/components/twin/TwinMap";
import { ConflictPanel } from "@/components/ops/ConflictPanel";
import { Inspector } from "@/components/ops/Inspector";
import { Timeline } from "@/components/ops/Timeline";
import { KpiBar } from "@/components/ops/KpiBar";

export const Route = createFileRoute("/conflict")({
  head: () => ({
    meta: [
      { title: "Conflict Focus — Vasai Road Junction Digital Twin" },
      {
        name: "description",
        content:
          "Focused view of predicted resource contention at Vasai Road Jn: contended junction, trains involved, separation and time to conflict.",
      },
      { property: "og:title", content: "Conflict Focus — Vasai Road Junction Digital Twin" },
      {
        property: "og:description",
        content: "Isolate a predicted junction contention and inspect every parameter behind it.",
      },
    ],
  }),
  component: ConflictScreen,
});

function ConflictScreen() {
  return (
    <main className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">Conflict focus</h1>
      <Timeline />
      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)_340px] gap-px bg-border">
        <ConflictPanel />
        <div className="min-h-[480px] bg-map">
          <TwinMap />
        </div>
        <Inspector />
      </div>
      <KpiBar />
    </main>
  );
}
