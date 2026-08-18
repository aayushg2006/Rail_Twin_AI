import { useTwin } from "@/twin/store";
import { fleetById } from "@/twin/scenario";
import { mmss, trainTypeLabel } from "@/twin/format";
import { routeFor } from "@/twin/engine";
import { Panel, PanelHead, Tag, toneText, type Tone } from "./primitives";

function stateTone(s: string): Tone {
  if (s === "HELD") return "critical";
  if (s === "REGULATED") return "warning";
  if (s === "DWELL") return "selected";
  if (s === "CLEARED") return "dim";
  return "neutral";
}

export function TrainList({ className }: { className?: string }) {
  const { view, prediction, selectedTrainId, selectTrain } = useTwin();

  const rows = Object.values(view.trains)
    .filter((t) => !t.finished)
    .map((ts) => {
      const train = fleetById[ts.trainId]!;
      const route = routeFor(view, ts.trainId);
      const conflicted = prediction.conflicts.some(
        (c) => c.trainA === ts.trainId || c.trainB === ts.trainId,
      );
      return { ts, train, route, conflicted };
    })
    .sort((a, b) => a.train.priority - b.train.priority);

  return (
    <Panel className={className}>
      <PanelHead title="Train register" meta={`${rows.length} active movements`} />
      <div className="min-h-0 overflow-auto">
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 bg-shell">
            <tr className="label-xs">
              {["Train", "Type", "Route", "Speed", "Delay", "Prio", "State"].map((h) => (
                <th key={h} className="border-b border-border px-2 py-1 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ ts, train, route, conflicted }) => (
              <tr
                key={ts.trainId}
                onClick={() => selectTrain(ts.trainId)}
                className={`cursor-pointer border-b border-border/50 hover:bg-panel-raised ${
                  selectedTrainId === ts.trainId ? "bg-panel-raised" : ""
                }`}
              >
                <td className="num px-2 py-1 text-[11.5px]">
                  <span className={conflicted ? "text-critical" : ""}>{train.number}</span>
                  <span className="ml-2 text-[10px] text-faint">{train.name}</span>
                </td>
                <td className="px-2 py-1">
                  <Tag tone={train.type === "FREIGHT" ? "freight" : "neutral"}>
                    {trainTypeLabel[train.type]}
                  </Tag>
                </td>
                <td className="px-2 py-1 text-[11px] text-muted-foreground">{route.label}</td>
                <td className="num px-2 py-1 text-[11.5px]">{Math.round(ts.speedKmh)}</td>
                <td
                  className={`num px-2 py-1 text-[11.5px] ${
                    ts.delaySec > 240 ? "text-critical" : ts.delaySec > 60 ? "text-warning" : ""
                  }`}
                >
                  +{mmss(ts.delaySec)}
                </td>
                <td className="num px-2 py-1 text-[11.5px]">P{train.priority}</td>
                <td className={`num px-2 py-1 text-[11px] ${toneText(stateTone(ts.state))}`}>
                  {ts.state}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
