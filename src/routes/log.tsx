import { createFileRoute } from "@tanstack/react-router";
import { DecisionLogTable, PerformancePanel } from "@/components/ops/PerformancePanel";
import { KpiBar } from "@/components/ops/KpiBar";

export const Route = createFileRoute("/log")({
  head: () => ({
    meta: [
      { title: "Record & Performance — Vasai Road Junction Digital Twin" },
      {
        name: "description",
        content:
          "Audit trail of controller decisions at Vasai Road Jn with baseline versus optimised network performance.",
      },
      { property: "og:title", content: "Record & Performance — Vasai Road Junction Digital Twin" },
      {
        property: "og:description",
        content: "Decision audit trail and baseline versus optimised KPI comparison.",
      },
    ],
  }),
  component: LogScreen,
});

function LogScreen() {
  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:overflow-hidden">
      <h1 className="sr-only">Decision record and performance</h1>
      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-px bg-border">
        <PerformancePanel />
        <DecisionLogTable />
      </div>
      <KpiBar />
    </main>
  );
}
