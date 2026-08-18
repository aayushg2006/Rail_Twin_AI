import { useCallback, useMemo, useRef, useState } from "react";
import type { Point } from "@/domain/types";
import { headingAt, toPolyline } from "@/twin/geometry";
import {
  amenities,
  footBridges,
  LINE_Y,
  MAP_H,
  MAP_W,
  PLATFORM_X0,
  PLATFORM_X1,
  platforms,
  resourceById,
  signals,
  stationTicks,
  STATION_X0,
  STATION_X1,
  tracks,
} from "@/twin/topology";
import { occupiedPlatforms, routeFor, trainPoint } from "@/twin/engine";
import { fleet, fleetById } from "@/twin/scenario";
import { useTwin } from "@/twin/store";
import { mmss, trainTypeLabel } from "@/twin/format";
import { AmenityGlyph, CompassRose, SignalGlyph } from "./RailGlyphs";

const TRACK_STYLE: Record<string, { stroke: string; width: number; dash?: string }> = {
  WESTERN_SLOW: { stroke: "var(--rail)", width: 2.4 },
  WESTERN_FAST: { stroke: "var(--rail-strong)", width: 2.6 },
  DIVA_BRANCH: { stroke: "var(--rail-strong)", width: 2.6 },
  FREIGHT_CHORD: { stroke: "var(--freight)", width: 2.2, dash: "10 5" },
  YARD: { stroke: "var(--rail)", width: 1.6, dash: "5 4" },
};

const TYPE_FILL: Record<string, string> = {
  EXPRESS: "var(--selected)",
  PASSENGER: "var(--primary)",
  LOCAL: "var(--rail-strong)",
  MEMU: "var(--ok)",
  FREIGHT: "var(--freight)",
  SHUNT: "var(--muted-foreground)",
};

export function TwinMap() {
  const {
    view,
    sim,
    prediction,
    layers,
    toggleLayer,
    selectedTrainId,
    selectTrain,
    selectedConflict,
    selectConflict,
    selection,
    select,
    focusMode,
    previewOption,
    previewPath,
    horizonOffset,
  } = useTwin();

  /** In focus mode only the trains and resource of the selected conflict stay lit. */
  const focusTrains = useMemo(() => {
    if (!focusMode || !selectedConflict) return null;
    return new Set([selectedConflict.trainA, selectedConflict.trainB].filter(Boolean));
  }, [focusMode, selectedConflict]);
  const dimmed = (id: string) => (focusTrains && !focusTrains.has(id) ? 0.18 : 1);

  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const vb = useMemo(() => {
    const w = MAP_W / zoom;
    const h = MAP_H / zoom;
    const x = (MAP_W - w) / 2 + pan.x;
    const y = (MAP_H - h) / 2 + pan.y;
    return `${x} ${y} ${w} ${h}`;
  }, [zoom, pan]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    setZoom((z) => Math.min(4, Math.max(0.75, z * (e.deltaY < 0 ? 1.12 : 0.89))));
  }, []);

  const occupancy = useMemo(() => occupiedPlatforms(view), [view]);

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const scale = MAP_W / zoom / rect.width;
    setPan({ x: d.px - (e.clientX - d.x) * scale, y: d.py - (e.clientY - d.y) * scale });
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  return (
    <div className="relative h-full w-full overflow-hidden bg-map">
      <svg
        ref={svgRef}
        viewBox={vb}
        className="h-full w-full cursor-grab touch-none active:cursor-grabbing"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        <defs>
          <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
            <path d="M60 0 H0 V60" fill="none" stroke="var(--map-deep)" strokeWidth="1" />
          </pattern>
          <pattern id="fob" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--critical)" strokeWidth="2" opacity="0.55" />
          </pattern>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0 L10 5 L0 10 z" fill="var(--border-strong)" />
          </marker>
        </defs>

        <rect x={0} y={0} width={MAP_W} height={MAP_H} fill="url(#grid)" opacity={0.6} />

        {layers.infrastructure && <StationEnvelope />}
        {layers.infrastructure && <Corridors />}

        {/* Tracks */}
        <g opacity={focusTrains ? 0.55 : 1}>
          {tracks.map((t) => {
            const st = TRACK_STYLE[t.kind]!;
            const on = selection?.kind === "track" && selection.id === t.id;
            return (
              <g
                key={t.id}
                className="cursor-pointer"
                onClick={(e) => {
                  e.stopPropagation();
                  select(on ? null : { kind: "track", id: t.id });
                }}
              >
                <polyline
                  points={toPolyline(t.path)}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={12}
                />
                <polyline
                  points={toPolyline(t.path)}
                  fill="none"
                  stroke={on ? "var(--selected)" : st.stroke}
                  strokeWidth={on ? st.width + 1.4 : st.width}
                  strokeDasharray={st.dash}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </g>
            );
          })}
        </g>

        {layers.infrastructure && (
          <g>
            {footBridges.map((f) => (
              <g key={f.id}>
                <rect x={f.x - 4} y={f.y0} width={8} height={f.y1 - f.y0} fill="url(#fob)" />
                <rect
                  x={f.x - 4}
                  y={f.y0}
                  width={8}
                  height={f.y1 - f.y0}
                  fill="none"
                  stroke="var(--critical)"
                  strokeOpacity={0.5}
                  strokeWidth={0.8}
                />
                <text
                  x={f.x}
                  y={f.y0 - 6}
                  textAnchor="middle"
                  fontSize={9}
                  fontFamily="var(--font-cond)"
                  fill="var(--muted-foreground)"
                >
                  {f.id}
                </text>
              </g>
            ))}
          </g>
        )}

        {/* Platforms */}
        <g opacity={focusTrains ? 0.6 : 1}>
          {platforms.map((p) => {
            const occ = occupancy[p.id];
            const on = selection?.kind === "platform" && selection.id === p.id;
            return (
              <g
                key={p.id}
                className="cursor-pointer"
                onClick={(e) => {
                  e.stopPropagation();
                  select(on ? null : { kind: "platform", id: p.id });
                }}
              >
                <rect
                  x={p.x}
                  y={p.y}
                  width={p.w}
                  height={p.h}
                  fill={occ ? "color-mix(in oklab, var(--selected) 16%, var(--platform))" : "var(--platform)"}
                  stroke={on ? "var(--selected)" : occ ? "var(--selected)" : "var(--border)"}
                  strokeWidth={on ? 2 : occ ? 1.2 : 1}
                />
                <text
                  x={p.x + 8}
                  y={p.y + 16}
                  fontSize={12}
                  fontFamily="var(--font-cond)"
                  letterSpacing="0.08em"
                  fill="var(--foreground)"
                >
                  {p.label.toUpperCase()}
                </text>
                <text
                  x={p.x + p.w - 8}
                  y={p.y + 16}
                  textAnchor="end"
                  fontSize={9.5}
                  fontFamily="var(--font-cond)"
                  fill="var(--muted-foreground)"
                >
                  {occ ? `OCCUPIED · ${occ}` : p.usage.toUpperCase()}
                </text>
              </g>
            );
          })}
        </g>

        {layers.infrastructure && (
          <g>
            {amenities.map((a) => (
              <g key={a.id} transform={`translate(${a.at.x} ${a.at.y})`} color="var(--muted-foreground)">
                <AmenityGlyph kind={a.kind} />
              </g>
            ))}
            {signals.map((s) => (
              <g key={s.id} transform={`translate(${s.at.x} ${s.at.y})`}>
                <SignalGlyph aspect={s.aspect} facing={s.facing} />
              </g>
            ))}
          </g>
        )}

        {layers.infrastructure && <StationTicks />}

        {/* Predicted paths */}
        {layers.predicted && (
          <g>
            {Object.entries(prediction.paths).map(([id, path]) => {
              if (path.length < 2) return null;
              const dim = selectedTrainId && selectedTrainId !== id;
              return (
                <polyline
                  key={id}
                  points={toPolyline(path)}
                  fill="none"
                  stroke={TYPE_FILL[fleetById[id]?.type ?? "LOCAL"]}
                  strokeOpacity={(dim ? 0.14 : 0.42) * dimmed(id)}
                  strokeWidth={selectedTrainId === id ? 2.4 : 1.4}
                  strokeDasharray="3 6"
                  strokeLinecap="round"
                />
              );
            })}
          </g>
        )}

        {/* Recommended / previewed trajectory */}
        {layers.decision && previewPath && previewPath.length > 1 && (
          <g>
            <polyline
              points={toPolyline(previewPath)}
              fill="none"
              stroke="var(--ok)"
              strokeOpacity={0.85}
              strokeWidth={2.6}
              strokeDasharray="8 5"
              strokeLinecap="round"
            />
            <text
              x={previewPath[0]!.x}
              y={previewPath[0]!.y - 26}
              fontSize={10}
              fontFamily="var(--font-cond)"
              letterSpacing="0.08em"
              fill="var(--ok)"
            >
              PROPOSED · {previewOption?.title.toUpperCase()}
            </text>
          </g>
        )}

        {/* Conflicts */}
        {layers.predicted && (
          <g>
            {prediction.conflicts.map((c) => {
              const active = selectedConflict?.id === c.id;
              const col = c.severity === "CRITICAL" ? "var(--critical)" : "var(--warning)";
              return (
                <g
                  key={c.id}
                  transform={`translate(${c.at.x} ${c.at.y})`}
                  className="cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    selectConflict(c.id);
                  }}
                >
                  <circle r={active ? 30 : 22} fill={col} fillOpacity={0.1} stroke={col} strokeWidth={active ? 1.6 : 1} />
                  {active && <circle r={38} fill="none" stroke={col} strokeWidth={0.8} strokeDasharray="4 5" />}
                  <path d="M0 -9 L9 7 L-9 7 Z" fill="none" stroke={col} strokeWidth={1.6} />
                  <text y={4} textAnchor="middle" fontSize={9} fontFamily="var(--font-mono)" fill={col}>
                    !
                  </text>
                  <text
                    y={-38}
                    textAnchor="middle"
                    fontSize={10}
                    fontFamily="var(--font-cond)"
                    letterSpacing="0.06em"
                    fill={col}
                  >
                    {c.resourceId} · T+{mmss(c.etaSec)}
                  </text>
                </g>
              );
            })}
          </g>
        )}

        {/* Trains */}
        {layers.live && (
          <g>
            {fleet.map((f) => {
              const st = view.trains[f.id];
              if (!st || st.finished) return null;
              const route = routeFor(view, f.id);
              const pt = trainPoint(view, f.id);
              const heading = (headingAt(route.path, st.s) * 180) / Math.PI;
              const selected = selectedTrainId === f.id;
              const len = f.type === "FREIGHT" ? 46 : f.type === "EXPRESS" ? 34 : 26;
              const col = TYPE_FILL[f.type]!;
              const stateStroke =
                st.state === "HELD"
                  ? "var(--critical)"
                  : st.state === "REGULATED"
                    ? "var(--warning)"
                    : st.state === "DWELL"
                      ? "var(--ok)"
                      : "var(--map-deep)";
              return (
                <g
                  key={f.id}
                  opacity={dimmed(f.id)}
                  className="cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    selectTrain(selected ? null : f.id);
                  }}
                >
                  <g transform={`translate(${pt.x} ${pt.y}) rotate(${heading})`}>
                    {selected && (
                      <circle r={26} fill="none" stroke="var(--selected)" strokeWidth={1} strokeDasharray="3 4" />
                    )}
                    <rect
                      x={-len / 2}
                      y={-6}
                      width={len}
                      height={12}
                      rx={2}
                      fill={col}
                      fillOpacity={0.9}
                      stroke={stateStroke}
                      strokeWidth={1.6}
                    />
                    <path d={`M${len / 2} 0 l-7 -5 v10 z`} fill="var(--map-deep)" fillOpacity={0.55} />
                  </g>
                  <g transform={`translate(${pt.x} ${pt.y})`}>
                    <text
                      y={-14}
                      textAnchor="middle"
                      fontSize={10.5}
                      fontFamily="var(--font-mono)"
                      fill={selected ? "var(--foreground)" : "var(--muted-foreground)"}
                    >
                      {f.id}
                    </text>
                    <text
                      y={22}
                      textAnchor="middle"
                      fontSize={9}
                      fontFamily="var(--font-cond)"
                      letterSpacing="0.06em"
                      fill={
                        st.state === "HELD"
                          ? "var(--critical)"
                          : st.state === "REGULATED"
                            ? "var(--warning)"
                            : "var(--muted-foreground)"
                      }
                    >
                      {trainTypeLabel[f.type]} · {Math.round(st.speedKmh)} KM/H · {st.state}
                    </text>
                  </g>
                </g>
              );
            })}
          </g>
        )}

        {/* Decision overlay */}
        {layers.decision && sim.appliedActions.length > 0 && (
          <g>
            {sim.appliedActions.map((a, i) => {
              const st = view.trains[a.trainId];
              if (!st || st.finished) return null;
              const pt = trainPoint(view, a.trainId);
              return (
                <g key={i} transform={`translate(${pt.x} ${pt.y})`}>
                  <circle r={19} fill="none" stroke="var(--ok)" strokeWidth={1.1} />
                  <text
                    x={22}
                    y={-16}
                    fontSize={9}
                    fontFamily="var(--font-cond)"
                    letterSpacing="0.06em"
                    fill="var(--ok)"
                  >
                    {a.kind.replace(/_/g, " ")}
                  </text>
                </g>
              );
            })}
          </g>
        )}

        <g transform={`translate(${MAP_W - 70} ${MAP_H - 70})`}>
          <g transform="rotate(90)">
            <CompassRose />
          </g>
        </g>
      </svg>

      <div className="pointer-events-none absolute top-3 left-3 flex flex-col gap-1">
        <div className="label-xs">Vasai Road Jn · operational schematic</div>
        <div className="num text-[11px] text-muted-foreground">
          {horizonOffset > 0 ? `PROJECTED T+${mmss(horizonOffset)}` : "LIVE"} · scale 1 unit = 10 m
          {focusTrains ? " · FOCUS" : ""}
        </div>
      </div>

      <div className="absolute top-3 right-3 flex items-center gap-1">
        {(
          [
            ["infrastructure", "Infra"],
            ["live", "Live"],
            ["predicted", "Predicted"],
            ["decision", "Decision"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => toggleLayer(key)}
            className={`font-cond border px-2 py-[3px] text-[10.5px] tracking-[0.1em] uppercase ${
              layers[key]
                ? "border-selected bg-selected/12 text-selected"
                : "border-border bg-panel text-faint"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="absolute bottom-3 left-3 flex items-center gap-1">
        {(
          [
            ["−", () => setZoom((z) => Math.max(0.75, z * 0.85))],
            ["+", () => setZoom((z) => Math.min(4, z * 1.18))],
          ] as const
        ).map(([label, fn]) => (
          <button
            key={label}
            onClick={fn}
            className="h-6 w-6 border border-border bg-panel text-muted-foreground hover:text-foreground"
          >
            {label}
          </button>
        ))}
        <button
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
          className="h-6 border border-border bg-panel px-2 label-xs hover:text-foreground"
        >
          Fit
        </button>
      </div>
    </div>
  );
}

function StationEnvelope() {
  return (
    <g>
      <rect
        x={STATION_X0}
        y={140}
        width={STATION_X1 - STATION_X0}
        height={600}
        fill="var(--map-deep)"
        fillOpacity={0.55}
        stroke="var(--border)"
        strokeDasharray="6 6"
      />
      <text
        x={STATION_X0 + 10}
        y={132}
        fontSize={10}
        fontFamily="var(--font-cond)"
        letterSpacing="0.14em"
        fill="var(--muted-foreground)"
      >
        VASAI ROAD JUNCTION — STATION LIMITS
      </text>
      <text
        x={PLATFORM_X0}
        y={758}
        fontSize={9}
        fontFamily="var(--font-cond)"
        letterSpacing="0.1em"
        fill="var(--muted-foreground)"
      >
        EAST CONCOURSE
      </text>
      <text
        x={PLATFORM_X1}
        y={132}
        textAnchor="end"
        fontSize={9}
        fontFamily="var(--font-cond)"
        letterSpacing="0.1em"
        fill="var(--muted-foreground)"
      >
        WEST CONCOURSE
      </text>
    </g>
  );
}

function Corridors() {
  return (
    <g fontFamily="var(--font-cond)" letterSpacing="0.1em" fill="var(--muted-foreground)">
      <line x1={1300} y1={LINE_Y.WSD - 60} x2={1560} y2={LINE_Y.WSD - 60} stroke="var(--border-strong)" markerEnd="url(#arrow)" />
      <text x={1300} y={LINE_Y.WSD - 70} fontSize={11}>
        TOWARD VIRAR / DAHANU ROAD (NORTH)
      </text>
      <line x1={300} y1={LINE_Y.WSD - 60} x2={40} y2={LINE_Y.WSD - 60} stroke="var(--border-strong)" markerEnd="url(#arrow)" />
      <text x={300} y={LINE_Y.WSD - 70} textAnchor="end" fontSize={11}>
        TOWARD BHAYANDAR / CHURCHGATE (SOUTH)
      </text>
      <text x={1300} y={LINE_Y.FRC - 18} fontSize={11}>
        GOODS CHORD / KONKAN &amp; SURAT FREIGHT
      </text>
      <text x={30} y={922} fontSize={11}>
        TOWARD DIVA JN — BHIWANDI · PANVEL · KALYAN
      </text>
    </g>
  );
}

function StationTicks() {
  return (
    <g>
      {stationTicks.map((t) => {
        const y = t.corridor === "DIVA" ? t.at.y : LINE_Y.WSD - 88;
        return (
          <g key={t.name} transform={`translate(${t.at.x} ${y})`}>
            <line x1={0} y1={-5} x2={0} y2={5} stroke="var(--border-strong)" strokeWidth={1.2} />
            <text
              y={t.corridor === "DIVA" ? 18 : -10}
              textAnchor="middle"
              fontSize={9}
              fontFamily="var(--font-cond)"
              letterSpacing="0.06em"
              fill="var(--muted-foreground)"
            >
              {t.name.toUpperCase()} · {t.km} KM
            </text>
          </g>
        );
      })}
      {Object.values(resourceById)
        .filter((r) => r.kind === "JUNCTION")
        .map((r) => (
          <g key={r.id} transform={`translate(${r.at.x} ${r.at.y})`}>
            <rect x={-9} y={-9} width={18} height={18} fill="none" stroke="var(--border-strong)" strokeDasharray="2 3" />
            <text
              x={12}
              y={-10}
              fontSize={9}
              fontFamily="var(--font-cond)"
              letterSpacing="0.06em"
              fill="var(--muted-foreground)"
            >
              {r.id}
            </text>
          </g>
        ))}
    </g>
  );
}
