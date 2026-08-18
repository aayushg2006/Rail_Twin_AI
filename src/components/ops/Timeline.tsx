import { useTwin, type SimSpeed } from "@/twin/store";
import { mmss } from "@/twin/format";
import { Btn } from "./primitives";

const TICKS: { label: string; offset: number }[] = [
  { label: "NOW", offset: 0 },
  { label: "T+2", offset: 120 },
  { label: "T+5", offset: 300 },
  { label: "T+7", offset: 420 },
  { label: "T+10", offset: 600 },
  { label: "T+15", offset: 900 },
];

export function Timeline() {
  const {
    horizonOffset,
    setHorizonOffset,
    playing,
    setPlaying,
    speed,
    setSpeed,
    stepForward,
    jumpToNextEvent,
    prediction,
  } = useTwin();

  const conflicts = prediction.conflicts.filter((c) => c.etaSec <= 900);

  return (
    <div className="shrink-0 bg-shell">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5">
        <Btn onClick={() => setPlaying(!playing)} variant={playing ? "default" : "primary"}>
          {playing ? "Pause" : "Run"}
        </Btn>
        {([1, 2, 5, 10] as SimSpeed[]).map((s) => (
          <Btn key={s} active={speed === s} onClick={() => setSpeed(s)}>
            {s}×
          </Btn>
        ))}
        <Btn onClick={() => stepForward(60)}>Step +60s</Btn>
        <Btn onClick={jumpToNextEvent}>Next event</Btn>
        <span className="label-xs ml-2">Prediction horizon</span>
        <span className="num text-[12px]">
          {horizonOffset > 0 ? `T+${mmss(horizonOffset)}` : "NOW"}
        </span>
        <span className="label-xs ml-auto">
          {horizonOffset > 0 ? "Inspecting a future network state" : "Live network state"}
        </span>
      </div>

      <div className="px-3 py-2">
        <div className="flex items-center gap-1">
          {TICKS.map((t) => (
            <Btn
              key={t.label}
              active={horizonOffset === t.offset}
              onClick={() => setHorizonOffset(t.offset)}
              className="min-w-[52px]"
            >
              {t.label}
            </Btn>
          ))}
          <input
            type="range"
            min={0}
            max={900}
            step={15}
            value={horizonOffset}
            onChange={(e) => setHorizonOffset(Number(e.target.value))}
            aria-label="Prediction horizon scrub"
            className="ml-3 h-1 flex-1 accent-[var(--selected)]"
          />
        </div>

        {/* Event band: predicted contention placed on the same 0–15 min axis. */}
        <div className="relative mt-2 h-7 border border-border bg-map">
          <div
            className="absolute top-0 bottom-0 w-px bg-selected"
            style={{ left: `${(horizonOffset / 900) * 100}%` }}
          />
          {conflicts.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setHorizonOffset(Math.max(0, Math.round(c.etaSec / 15) * 15))}
              title={`${c.resourceLabel} · ${c.trainA}${c.trainB ? ` / ${c.trainB}` : ""}`}
              className="absolute top-1 bottom-1 w-[3px]"
              style={{
                left: `${Math.min(99.6, (c.etaSec / 900) * 100)}%`,
                background: c.severity === "CRITICAL" ? "var(--critical)" : "var(--warning)",
              }}
            />
          ))}
          <span className="label-xs absolute top-1 left-2">Predicted events</span>
          <span className="label-xs absolute top-1 right-2">15 min</span>
        </div>
      </div>
    </div>
  );
}
