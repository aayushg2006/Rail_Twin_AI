import { useTwin, type SimSpeed } from "@/twin/store";
import { mmss } from "@/twin/format";
import { Btn } from "./primitives";

/**
 * Run/pause, speed, and a single scrub bar (#6). The fixed T+2 / T+5 / T+10 /
 * T+15 buttons and the Step +60s / Next event controls are gone - the slider
 * already expresses everything they did, continuously.
 */
export function Timeline() {
  const { bundle, horizonOffset, setHorizonOffset, playing, setPlaying, speed, setSpeed, conflicts } =
    useTwin();
  const live = bundle?.clockMode === "LIVE";

  const within = conflicts.filter((c) => c.etaSec <= 900);

  return (
    <div className="shrink-0 bg-shell">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5">
        <Btn disabled={live} onClick={() => setPlaying(!playing)} variant={playing ? "default" : "primary"}>
          {playing ? "Pause" : "Run"}
        </Btn>
        {([1, 2, 5, 10] as SimSpeed[]).map((s) => (
          <Btn key={s} disabled={live} active={speed === s} onClick={() => setSpeed(s)}>
            {s}×
          </Btn>
        ))}
        <span className="label-xs ml-3">Looking ahead</span>
        <span className="num text-[12px]">
          {horizonOffset > 0 ? `+${mmss(horizonOffset)}` : "now"}
        </span>
        <span className="label-xs ml-auto">
          {horizonOffset > 0 ? "Projected network state" : "Live network state"}
        </span>
      </div>

      <div className="px-3 py-2">
        <input
          type="range"
          min={0}
          max={900}
          step={15}
          value={horizonOffset}
          onChange={(e) => setHorizonOffset(Number(e.target.value))}
          aria-label="Look ahead"
          className="h-1 w-full accent-[var(--selected)]"
        />

        {/* Predicted contention on the same 0-15 minute axis as the slider. */}
        <div className="relative mt-2 h-6 border border-border bg-map">
          <div
            className="absolute top-0 bottom-0 w-px bg-selected"
            style={{ left: `${(horizonOffset / 900) * 100}%` }}
          />
          {within.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setHorizonOffset(Math.max(0, Math.round(c.etaSec / 15) * 15))}
              title={`${c.resourceLabel} · ${mmss(c.etaSec)}`}
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
