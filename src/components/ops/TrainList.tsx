import { useTwin } from "@/twin/store";
import { clockShort, lateness } from "@/twin/format";
import { CLASS_LABEL } from "@/twin/projection";
import { Panel, PanelHead, Tag, toneText, type Tone } from "./primitives";

function stateTone(s: string): Tone {
  if (s === "HELD") return "critical";
  if (s === "REGULATED") return "warning";
  if (s === "DWELL") return "selected";
  return "neutral";
}

/**
 * The register. Train number and name only (#3) — the "· synthetic" and
 * "· snapshot" provenance suffixes are gone from every row; provenance for the
 * fleet as a whole is stated once in the header instead.
 */
export function TrainList({ className }: { className?: string }) {
  const { activeTrains, conflicts, selectedTrainId, selectTrain, bundle } = useTwin();

  const conflicted = new Set(conflicts.flatMap((c) => [c.trainA, c.trainB]));
  const observed = new Set((bundle?.liveData.observations ?? []).map((o) => o.number));

  return (
    <Panel className={className}>
      <PanelHead
        title="Train register"
        meta={`${activeTrains.length} on the ground · ${bundle?.kpis.scheduledAhead ?? 0} booked ahead`}
      />
      <div className="min-h-0 overflow-auto">
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 bg-shell">
            <tr className="label-xs">
              {["Train", "Type", "Route", "Booked", "Speed", "Running", "State"].map((h) => (
                <th key={h} className="border-b border-border px-2 py-1 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {activeTrains.map((t) => (
              <tr
                key={t.trainId}
                onClick={() => selectTrain(t.trainId)}
                className={`cursor-pointer border-b border-border/50 hover:bg-panel-raised ${
                  selectedTrainId === t.trainId ? "bg-panel-raised" : ""
                }`}
              >
                <td className="num px-2 py-1 text-[11.5px]">
                  <span className={conflicted.has(t.trainId) ? "text-critical" : ""}>
                    {t.number}
                  </span>
                  <span className="ml-2 text-[10px] text-faint">{t.name}</span>
                </td>
                <td className="px-2 py-1">
                  <Tag tone={t.category === "FREIGHT" ? "freight" : "neutral"}>
                    {CLASS_LABEL[t.serviceClass] ?? t.category}
                  </Tag>
                </td>
                <td className="px-2 py-1 text-[11px] text-muted-foreground">
                  {t.arrivalCorridor} → {t.departureCorridor}
                  {t.platformId ? ` · ${t.platformId}` : ""}
                </td>
                <td className="num px-2 py-1 text-[11px] text-muted-foreground">
                  {clockShort(bookedMs(bundle?.simState.epochStartMs, bundle?.serviceSeconds, t.bookedDepSec))}
                </td>
                <td className="num px-2 py-1 text-[11.5px]">{Math.round(t.speedKmh)}</td>
                <td
                  className={`num px-2 py-1 text-[11.5px] ${
                    t.latenessSec > 300
                      ? "text-critical"
                      : t.latenessSec > 60
                        ? "text-warning"
                        : t.latenessSec < -30
                          ? "text-ok"
                          : ""
                  }`}
                >
                  {lateness(t.latenessSec)}
                  {observed.has(t.number) && (
                    <span className="ml-1 text-[9px] text-selected" title="Live observation">
                      ●
                    </span>
                  )}
                </td>
                <td className={`num px-2 py-1 text-[11px] ${toneText(stateTone(t.state))}`}>
                  {t.state}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/** Booked departure is a time of day; turn it back into a wall-clock instant. */
function bookedMs(
  epochStartMs: number | undefined,
  serviceSeconds: number | undefined,
  bookedDepSec: number,
): number {
  if (epochStartMs === undefined || serviceSeconds === undefined) return 0;
  return epochStartMs + (bookedDepSec - serviceSeconds) * 1000;
}
