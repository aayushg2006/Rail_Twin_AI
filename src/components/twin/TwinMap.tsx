import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Conflict, ScreenPoint, TrainSnapshot } from "@/domain/types";
import { useTwin } from "@/twin/store";
import {
  CLASS_COLOUR,
  CLASS_LEGEND,
  LINE_Y,
  lineY,
  platformY,
  VIEW_H,
  VIEW_W,
  type Projector,
} from "@/twin/projection";
import { GEO_WAY_STYLE, type GeoProjector } from "@/twin/geoprojection";
import { mmss } from "@/twin/format";
import { GeoMap } from "./GeoMap";

const MIN_ZOOM = 0.7;
const MAX_ZOOM = 8;

interface LineStyle {
  stroke: string;
  width: number;
  dash?: string;
}

const DEFAULT_LINE_STYLE: LineStyle = { stroke: "var(--rail)", width: 2.2 };

const LINE_STYLE: Record<string, LineStyle> = {
  SUBURBAN_SLOW: { stroke: "var(--rail)", width: 2.2 },
  SUBURBAN_FAST: { stroke: "var(--rail-strong)", width: 2.6 },
  MAIN_THROUGH: { stroke: "var(--rail-strong)", width: 2.8 },
  BRANCH: { stroke: "var(--branch)", width: 2.8 },
  FREIGHT: { stroke: "var(--freight)", width: 2.2, dash: "12 6" },
  YARD: { stroke: "var(--rail)", width: 1.6, dash: "5 5" },
};

function poly(points: (ScreenPoint | undefined)[]): string {
  return points
    .filter((p): p is ScreenPoint => !!p && Number.isFinite(p.x) && Number.isFinite(p.y))
    .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
}

export function TwinMap() {
  const {
    bundle,
    projector,
    layers,
    toggleLayer,
    activeTrains,
    conflicts,
    selectedTrainId,
    selectTrain,
    selectedConflict,
    selectConflict,
    selection,
    select,
    focusMode,
    horizonOffset,
    previewOption,
    geoProjector,
    mapView,
    setMapView,
  } = useTwin();

  const geographic = mapView === "GEOGRAPHIC" && !!geoProjector?.available;

  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<ScreenPoint>({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  // The frame the view opens on. Geographic opens on the junction rather than
  // the whole modelled extent, which is 16 km of approach either side.
  const base = useMemo(
    () =>
      geographic && geoProjector
        ? geoProjector.home
        : { x: 0, y: 0, w: VIEW_W, h: VIEW_H },
    [geographic, geoProjector],
  );

  const view = useMemo(() => {
    const w = base.w / zoom;
    const h = base.h / zoom;
    return {
      x: base.x + (base.w - w) / 2 + pan.x,
      y: base.y + (base.h - h) / 2 + pan.y,
      w,
      h,
    };
  }, [zoom, pan, base]);

  /** Client pixel -> SVG user units, so zoom can be anchored on the cursor. */
  const toSvg = useCallback(
    (clientX: number, clientY: number): ScreenPoint => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return { x: base.x + base.w / 2, y: base.y + base.h / 2 };
      return {
        x: view.x + ((clientX - rect.left) / rect.width) * view.w,
        y: view.y + ((clientY - rect.top) / rect.height) * view.h,
      };
    },
    [view, base],
  );

  /**
   * Cursor-anchored zoom (#21). The old wheel handler always recentred on the
   * middle of the map, so the point under the pointer drifted away as you
   * zoomed. Here the world point under the cursor is held fixed, and a trackpad
   * pinch (ctrlKey) is scaled more gently than a mouse wheel notch.
   */
  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const anchor = toSvg(e.clientX, e.clientY);
      const intensity = e.ctrlKey ? 0.01 : 0.0022;
      const next = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, zoom * Math.exp(-e.deltaY * intensity)),
      );
      if (next === zoom) return;
      const w = base.w / next;
      const h = base.h / next;
      const fx = (anchor.x - view.x) / view.w;
      const fy = (anchor.y - view.y) / view.h;
      setPan({
        x: anchor.x - fx * w - (base.w - w) / 2,
        y: anchor.y - fy * h - (base.h - h) / 2,
      });
      setZoom(next);
    },
    [toSvg, view, zoom, base],
  );

  const onPointerDown = (e: React.PointerEvent) => {
    if ((e.target as Element).closest("[data-pickable]")) return;
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const scale = view.w / rect.width;
    setPan({ x: d.px - (e.clientX - d.x) * scale, y: d.py - (e.clientY - d.y) * scale });
  };
  const endDrag = () => {
    drag.current = null;
  };

  const focusIds = useMemo(() => {
    if (!focusMode || !selectedConflict) return null;
    return new Set([selectedConflict.trainA, selectedConflict.trainB].filter(Boolean));
  }, [focusMode, selectedConflict]);

  const dim = (id: string) => (focusIds && !focusIds.has(id) ? 0.12 : 1);

  const actionByTrain = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of bundle?.simState.appliedActions ?? []) {
      if (a.kind === "HOLD") map.set(a.trainId, `HOLD ${Math.round(a.holdSec ?? 0)}s`);
      else if (a.kind === "SPEED_REGULATION")
        map.set(a.trainId, `${Math.round(a.speedKmh ?? 0)} KM/H`);
      else if (a.kind === "PLATFORM_REASSIGNMENT")
        map.set(a.trainId, `→ ${a.platformId ?? "RE-PLATFORM"}`);
    }
    return map;
  }, [bundle]);

  // Reframe only when the focused episode changes. Bundle refreshes must not
  // steal a controller's manual pan/zoom while they inspect the same episode.
  useEffect(() => {
    if (!focusMode || !selectedConflict || !projector || !bundle) return;
    const points: ScreenPoint[] = [];
    for (const id of [selectedConflict.trainA, selectedConflict.trainB]) {
      const train = bundle.simState.trains[id];
      if (train?.at) points.push(projector.point(train.at, train.line));
      for (const at of bundle.prediction.paths[id] ?? []) {
        points.push(projector.point(at, train?.line ?? null));
      }
    }
    points.push(projector.point(
      selectedConflict.at,
      bundle.simState.trains[selectedConflict.trainA]?.line ?? null,
    ));
    if (!points.length) return;
    const minX = Math.min(...points.map((point) => point.x));
    const maxX = Math.max(...points.map((point) => point.x));
    const minY = Math.min(...points.map((point) => point.y));
    const maxY = Math.max(...points.map((point) => point.y));
    const nextZoom = Math.min(4.5, Math.max(1,
      Math.min(base.w / Math.max(280, maxX - minX + 180),
        base.h / Math.max(180, maxY - minY + 140))));
    setZoom(nextZoom);
    setPan({
      x: (minX + maxX) / 2 - (base.x + base.w / 2),
      y: (minY + maxY) / 2 - (base.y + base.h / 2),
    });
  }, [focusMode, selectedConflict?.id, projector, base]);

  if (!projector || !bundle) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-map">
        <p className="label-xs text-muted-foreground">Waiting for the twin…</p>
      </div>
    );
  }

  if (geographic) return <GeoMap />;

  // Both projectors satisfy the same interface, so the movement layers are
  // written once and drawn against whichever view is active.
  const active: Projector = geographic && geoProjector ? geoProjector : projector;

  return (
    <div className="relative h-full w-full overflow-hidden bg-map">
      <svg
        ref={svgRef}
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        className="h-full w-full cursor-grab touch-none active:cursor-grabbing"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        onClick={() => select(null)}
      >
        <defs>
          <pattern id="grid" width="70" height="70" patternUnits="userSpaceOnUse">
            <path d="M70 0 H0 V70" fill="none" stroke="var(--map-deep)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect
          x={-VIEW_W}
          y={-VIEW_H}
          width={VIEW_W * 3}
          height={VIEW_H * 3}
          fill="url(#grid)"
          opacity={0.5}
        />

        {geographic && geoProjector ? (
          <GeoInfrastructure projector={geoProjector} show={layers.infrastructure} />
        ) : (
          <>
            {layers.infrastructure && (
              <g opacity={focusIds ? 0.28 : 1}>
                <StationEnvelope projector={projector} />
                <Platforms projector={projector} />
                <CorridorLabels projector={projector} />
              </g>
            )}
            <Lines projector={projector} focused={!!focusIds} selection={selection} select={select} />
            <SchematicTurnouts projector={projector} focused={!!focusIds} />
          </>
        )}

        {layers.predicted && <PredictedPaths projector={active} dim={dim} />}
        {layers.decision && previewOption && (
          <ProposedPath projector={active} trainId={previewOption.action.trainId} />
        )}
        {layers.predicted && (
          <Conflicts
            projector={active}
            conflicts={conflicts}
            selectedId={selectedConflict?.id ?? null}
            focused={focusMode}
            onSelect={selectConflict}
          />
        )}
        {layers.live && (
          <Trains
            projector={active}
            trains={activeTrains}
            selectedId={selectedTrainId}
            onSelect={selectTrain}
            dim={dim}
            actions={actionByTrain}
          />
        )}
      </svg>

      <div className="pointer-events-none absolute top-3 left-3 flex flex-col gap-1">
        <div className="label-xs">Vasai Road Jn · operational schematic</div>
        <div className="num text-[11px] text-muted-foreground">
          {horizonOffset > 0 ? `PROJECTED T+${mmss(horizonOffset)}` : "LIVE"} ·{" "}
          {geographic ? "real track geometry" : "schematic"} · distances in metres
          {focusIds ? " · FOCUS" : ""}
        </div>
        {geographic && geoProjector?.geometry?.licence && (
          <div className="text-[9px] text-faint">{geoProjector.geometry.licence}</div>
        )}
      </div>

      <div className="absolute top-3 right-3 flex items-center gap-1">
        {geoProjector?.available && (
          <div className="mr-2 flex items-center gap-px">
            {(
              [
                ["GEOGRAPHIC", "Real map"],
                ["SCHEMATIC", "Schematic"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMapView(value)}
                className={`font-cond border px-2 py-[3px] text-[10.5px] tracking-[0.1em] uppercase ${
                  mapView === value
                    ? "border-selected bg-selected/12 text-selected"
                    : "border-border bg-panel text-faint"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
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

      <Legend />

      <div className="absolute bottom-3 left-3 flex items-center gap-1">
        <button
          onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z / 1.3))}
          className="h-6 w-6 border border-border bg-panel text-muted-foreground hover:text-foreground"
          aria-label="Zoom out"
        >
          −
        </button>
        <button
          onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * 1.3))}
          className="h-6 w-6 border border-border bg-panel text-muted-foreground hover:text-foreground"
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          onClick={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
          className="h-6 border border-border bg-panel px-2 label-xs hover:text-foreground"
        >
          Fit
        </button>
        <span className="num ml-2 text-[10px] text-faint">{zoom.toFixed(1)}×</span>
      </div>
    </div>
  );
}

/**
 * The real Vasai Road, drawn from OpenStreetMap: 39 track ways, the six actual
 * turnouts, five signals, and the three island platforms tagged 2;3, 4;5 and
 * 6;7. This is the same railway RailRadar shows, at the same coordinates.
 */
function GeoInfrastructure({
  projector,
  show,
}: {
  projector: GeoProjector;
  show: boolean;
}) {
  const geo = projector.geometry;
  const { select, selection, bundle } = useTwin();
  if (!geo) return null;
  const blocked = new Set(bundle?.simState.blockedResources ?? []);

  return (
    <g>
      {geo.ways.map((way) => {
        const style = GEO_WAY_STYLE[way.kind] ?? GEO_WAY_STYLE["OTHER"]!;
        return (
          <polyline
            key={way.id}
            points={poly(way.local.map((p) => projector.local(p.x, p.y)))}
            fill="none"
            stroke={style.stroke}
            strokeWidth={style.width}
            strokeDasharray={style.dash}
            strokeOpacity={style.opacity ?? 1}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        );
      })}

      {show &&
        geo.platforms.map((pf) => {
          const pts = pf.local.map((p) => projector.local(p.x, p.y));
          const out = blocked.has(pf.faces[0] ?? "") || blocked.has(pf.faces[1] ?? "");
          const on =
            selection?.kind === "platform" && pf.faces.includes(selection.id);
          const cx = pts.reduce((a, p) => a + p.x, 0) / Math.max(1, pts.length);
          const cy = pts.reduce((a, p) => a + p.y, 0) / Math.max(1, pts.length);
          return (
            <g
              key={pf.ref ?? pf.faces.join()}
              data-pickable
              className="cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                const face = pf.faces[0];
                select(on || !face ? null : { kind: "platform", id: face });
              }}
            >
              <polygon
                points={poly(pts)}
                fill={out ? "var(--critical)" : "var(--platform)"}
                fillOpacity={out ? 0.3 : 0.55}
                stroke={on ? "var(--selected)" : out ? "var(--critical)" : "var(--border-strong)"}
                strokeWidth={on ? 1.6 : 1}
              />
              <text
                x={cx}
                y={cy + 3}
                textAnchor="middle"
                fontSize={7}
                fontFamily="var(--font-cond)"
                letterSpacing="0.06em"
                fill="var(--foreground)"
              >
                {pf.faces.join(" / ")}
              </text>
            </g>
          );
        })}

      {show && (
        <g>
          {geo.switches.map((sw) => {
            const p = projector.local(sw.local.x, sw.local.y);
            return (
              <g key={sw.id}>
                <path
                  d={`M${p.x - 3} ${p.y + 3} L${p.x} ${p.y - 3} L${p.x + 3} ${p.y + 3}`}
                  fill="none"
                  stroke="var(--warning)"
                  strokeWidth={1.1}
                />
              </g>
            );
          })}
          {geo.signals.map((sig) => {
            const p = projector.local(sig.local.x, sig.local.y);
            return (
              <circle key={sig.id} cx={p.x} cy={p.y} r={2} fill="var(--ok)" fillOpacity={0.8} />
            );
          })}
        </g>
      )}

      {show && geo.station && (
        <text
          x={projector.latLng(geo.station.lat, geo.station.lng).x}
          y={projector.latLng(geo.station.lat, geo.station.lng).y - 26}
          textAnchor="middle"
          fontSize={10}
          fontFamily="var(--font-cond)"
          letterSpacing="0.14em"
          fill="var(--muted-foreground)"
        >
          VASAI ROAD JN
        </text>
      )}
    </g>
  );
}

function StationEnvelope({ projector }: { projector: Projector }) {
  const box = projector.stationBox();
  return (
    <g>
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        fill="var(--map-deep)"
        fillOpacity={0.5}
        stroke="var(--border)"
        strokeDasharray="6 6"
      />
      <text
        x={box.x + 10}
        y={box.y - 10}
        fontSize={11}
        fontFamily="var(--font-cond)"
        letterSpacing="0.14em"
        fill="var(--muted-foreground)"
      >
        VASAI ROAD JUNCTION — STATION LIMITS
      </text>
    </g>
  );
}

function Lines({
  projector,
  focused,
  selection,
  select,
}: {
  projector: Projector;
  focused: boolean;
  selection: ReturnType<typeof useTwin>["selection"];
  select: ReturnType<typeof useTwin>["select"];
}) {
  return (
    <g opacity={focused ? 0.35 : 1}>
      {projector.network.lines.map((line) => {
        const style = LINE_STYLE[line.kind] ?? DEFAULT_LINE_STYLE;
        const path = projector.linePath(line.id);
        return (
          <g key={line.id}>
            <polyline
              points={poly(path)}
              fill="none"
              stroke={style.stroke}
              strokeWidth={style.width}
              strokeDasharray={style.dash}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <text
              x={(path[path.length - 1]?.x ?? 0) - 6}
              y={(path[path.length - 1]?.y ?? lineY(line.id)) - 8}
              textAnchor="end"
              fontSize={9}
              fontFamily="var(--font-cond)"
              letterSpacing="0.08em"
              fill="var(--faint)"
            >
              {line.id}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function SchematicTurnouts({ projector, focused }: { projector: Projector; focused: boolean }) {
  const north = projector.resourceSpan("J-N", "THU");
  const south = projector.resourceSpan("J-S", "THU");
  const branch = projector.resourceSpan("J-B", "BRD");
  return (
    <g opacity={focused ? 0.3 : 0.9} fill="none" stroke="var(--warning)" strokeWidth={1.25}>
      {north && (
        <g>
          <path d={`M${north[0].x} ${LINE_Y.SLD} L${north[1].x} ${LINE_Y.SLU}`} />
          <path d={`M${north[0].x + 28} ${LINE_Y.FSD} L${north[1].x + 28} ${LINE_Y.FSU}`} />
          <path d={`M${north[0].x + 56} ${LINE_Y.THD} L${north[1].x + 56} ${LINE_Y.THU}`} />
        </g>
      )}
      {south && (
        <g>
          <path d={`M${south[1].x} ${LINE_Y.SLD} L${south[0].x} ${LINE_Y.SLU}`} />
          <path d={`M${south[1].x - 28} ${LINE_Y.FSD} L${south[0].x - 28} ${LINE_Y.FSU}`} />
          <path d={`M${south[1].x - 56} ${LINE_Y.THD} L${south[0].x - 56} ${LINE_Y.THU}`} />
        </g>
      )}
      {branch && (
        <>
          <circle cx={branch[0].x} cy={branch[0].y} r={3.5} fill="var(--warning)" />
          <circle cx={branch[1].x} cy={branch[1].y} r={3.5} fill="var(--warning)" />
        </>
      )}
    </g>
  );
}

function Platforms({ projector }: { projector: Projector }) {
  const { selection, select, bundle } = useTwin();
  const blocked = new Set(bundle?.simState.blockedResources ?? []);
  const occupied = new Map<string, string>();
  for (const t of Object.values(bundle?.simState.trains ?? {})) {
    if (t.admitted && !t.finished && t.platformId && t.state === "DWELL") {
      occupied.set(t.platformId, t.number);
    }
  }
  return (
    <g>
      {projector.network.platforms.map((pf) => {
        const span = projector.resourceSpan(pf.id);
        if (!span) return null;
        const [a, b] = span;
        const y = platformY(pf.id);
        const on = selection?.kind === "platform" && selection.id === pf.id;
        const isBlocked = blocked.has(pf.id);
        const occ = occupied.get(pf.id);
        return (
          <g
            key={pf.id}
            data-pickable
            className="cursor-pointer"
            onClick={(e) => {
              e.stopPropagation();
              select(on ? null : { kind: "platform", id: pf.id });
            }}
          >
            <rect
              x={a.x}
              y={y - 22}
              width={b.x - a.x}
              height={19}
              fill={
                isBlocked
                  ? "color-mix(in oklab, var(--critical) 22%, var(--platform))"
                  : occ
                    ? "color-mix(in oklab, var(--selected) 16%, var(--platform))"
                    : "var(--platform)"
              }
              stroke={
                on ? "var(--selected)" : isBlocked ? "var(--critical)" : "var(--border)"
              }
              strokeWidth={on || isBlocked ? 1.6 : 1}
            />
            <text
              x={a.x + 7}
              y={y - 8}
              fontSize={11}
              fontFamily="var(--font-cond)"
              letterSpacing="0.08em"
              fill="var(--foreground)"
            >
              {pf.label.toUpperCase()}
            </text>
            <text
              x={b.x - 7}
              y={y - 8}
              textAnchor="end"
              fontSize={9}
              fontFamily="var(--font-cond)"
              fill={isBlocked ? "var(--critical)" : "var(--muted-foreground)"}
            >
              {isBlocked ? "OUT OF USE" : occ ? `OCCUPIED · ${occ}` : pf.usage.toUpperCase()}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/**
 * Corridor labels only — the intermediate Diva branch station names are gone
 * (#24), leaving one clear statement of where the branch goes.
 */
function CorridorLabels({ projector }: { projector: Projector }) {
  const corridors = projector.network.corridors;
  return (
    <g fontFamily="var(--font-cond)" letterSpacing="0.1em" fill="var(--muted-foreground)">
      <text x={VIEW_W - 120} y={LINE_Y.SLD - 74} textAnchor="end" fontSize={11}>
        {corridors["NORTH"]?.shortLabel ?? "TOWARD VIRAR"}
      </text>
      <text x={120} y={LINE_Y.SLD - 74} fontSize={11}>
        {corridors["SOUTH"]?.shortLabel ?? "TOWARD MUMBAI"}
      </text>
      <text x={70} y={LINE_Y.BRD + 250} fontSize={12} fill="var(--branch)">
        {corridors["DIVA"]?.shortLabel ?? "TOWARD DIVA JN"}
      </text>
      <text x={VIEW_W - 120} y={LINE_Y.GDC - 12} textAnchor="end" fontSize={10}>
        GOODS AVOIDING CHORD
      </text>
      <text x={200} y={LINE_Y.GYL + 22} fontSize={10}>
        GOODS YARD
      </text>
    </g>
  );
}

function PredictedPaths({
  projector,
  dim,
}: {
  projector: Projector;
  dim: (id: string) => number;
}) {
  const { bundle, selectedTrainId } = useTwin();
  const trains = bundle?.simState.trains ?? {};
  return (
    <g>
      {Object.entries(bundle?.prediction.paths ?? {}).map(([id, chain]) => {
        const train = trains[id];
        if (!train || !train.admitted || chain.length < 2) return null;
        const pts = chain.map((c) => projector.point(c, train.line));
        const faded = selectedTrainId && selectedTrainId !== id;
        return (
          <polyline
            key={id}
            points={poly(pts)}
            fill="none"
            stroke={CLASS_COLOUR[train.serviceClass] ?? "var(--rail-strong)"}
            strokeOpacity={(faded ? 0.1 : 0.4) * dim(id)}
            strokeWidth={selectedTrainId === id ? 2.6 : 1.4}
            strokeDasharray="4 7"
            strokeLinecap="round"
          />
        );
      })}
    </g>
  );
}

function ProposedPath({ projector, trainId }: { projector: Projector; trainId: string }) {
  const { bundle } = useTwin();
  const chain = bundle?.prediction.paths[trainId];
  const train = bundle?.simState.trains[trainId];
  if (!chain || chain.length < 2 || !train) return null;
  const pts = chain.map((c) => projector.point(c, train.line));
  return (
    <polyline
      points={poly(pts)}
      fill="none"
      stroke="var(--ok)"
      strokeOpacity={0.9}
      strokeWidth={2.8}
      strokeDasharray="9 5"
      strokeLinecap="round"
    />
  );
}

/**
 * Conflicts are drawn ON the contended stretch of track, coloured and
 * symbolised by severity (#27), rather than as one generic triangle floating at
 * a resource's centre point.
 */
function Conflicts({
  projector,
  conflicts,
  selectedId,
  focused,
  onSelect,
}: {
  projector: Projector;
  conflicts: Conflict[];
  selectedId: string | null;
  focused: boolean;
  onSelect: (id: string) => void;
}) {
  const { bundle } = useTwin();
  const trains = bundle?.simState.trains ?? {};
  return (
    <g>
      {conflicts.map((c) => {
        const line = trains[c.trainA]?.line ?? null;
        const span = projector.resourceSpan(c.resourceId, line);
        const at = projector.point(c.at, line);
        const critical = c.severity === "CRITICAL";
        const colour = critical ? "var(--critical)" : "var(--warning)";
        const active = selectedId === c.id;
        const opacity = focused && !active ? 0.08 : 1;
        return (
          <g
            key={c.id}
            data-pickable
            className="cursor-pointer"
            onClick={(e) => {
              e.stopPropagation();
              onSelect(c.id);
            }}
            opacity={opacity}
          >
            {span && (
              <polyline
                points={poly(span)}
                fill="none"
                stroke={colour}
                strokeOpacity={active ? 0.95 : 0.6}
                strokeWidth={active ? 9 : 6}
                strokeLinecap="round"
              />
            )}
            <g transform={`translate(${at.x} ${at.y - 26})`}>
              {active && (
                <circle r={17} fill="none" stroke={colour} strokeWidth={1} strokeDasharray="3 4" />
              )}
              {critical ? (
                <path d="M0 -10 L10 8 L-10 8 Z" fill={colour} fillOpacity={0.22} stroke={colour} strokeWidth={1.6} />
              ) : (
                <circle r={9} fill={colour} fillOpacity={0.18} stroke={colour} strokeWidth={1.6} />
              )}
              <text
                y={4}
                textAnchor="middle"
                fontSize={9}
                fontFamily="var(--font-mono)"
                fill={colour}
              >
                !
              </text>
              <text
                y={-16}
                textAnchor="middle"
                fontSize={10}
                fontFamily="var(--font-cond)"
                letterSpacing="0.05em"
                fill={colour}
              >
                {mmss(c.etaSec)}
              </text>
            </g>
          </g>
        );
      })}
    </g>
  );
}

/**
 * Trains are DOTS (#5), coloured by service class, sized by class so a goods
 * rake reads differently from a local. The old rotated rectangles were 260-460 m
 * long at map scale, which made the position a smear rather than a point.
 */
function Trains({
  projector,
  trains,
  selectedId,
  onSelect,
  dim,
  actions,
}: {
  projector: Projector;
  trains: TrainSnapshot[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  dim: (id: string) => number;
  actions: Map<string, string>;
}) {
  return (
    <g>
      {trains.map((t) => {
        if (!t.at) return null;
        const p = projector.point(t.at, t.line);
        const colour = CLASS_COLOUR[t.serviceClass] ?? "var(--rail-strong)";
        const selected = selectedId === t.trainId;
        const radius = t.category === "FREIGHT" ? 6.5 : t.category === "EXPRESS" ? 6 : 5;
        const held = t.state === "HELD";
        const regulated = t.state === "REGULATED";
        const action = actions.get(t.trainId);
        const ring = held ? "var(--critical)" : regulated ? "var(--warning)" : null;
        return (
          <g
            key={t.trainId}
            data-pickable
            opacity={dim(t.trainId)}
            className="cursor-pointer"
            onClick={(e) => {
              e.stopPropagation();
              onSelect(selected ? null : t.trainId);
            }}
          >
            {selected && (
              <circle cx={p.x} cy={p.y} r={16} fill="none" stroke="var(--selected)" strokeWidth={1} strokeDasharray="3 4" />
            )}
            {ring && (
              <circle cx={p.x} cy={p.y} r={radius + 4.5} fill="none" stroke={ring} strokeWidth={1.8} />
            )}
            <circle
              cx={p.x}
              cy={p.y}
              r={radius}
              fill={colour}
              stroke="var(--map-deep)"
              strokeWidth={1.4}
            />
            <text
              x={p.x}
              y={p.y - radius - 7}
              textAnchor="middle"
              fontSize={10}
              fontFamily="var(--font-mono)"
              fill={selected ? "var(--foreground)" : "var(--muted-foreground)"}
            >
              {t.number}
            </text>
            <text
              x={p.x}
              y={p.y + radius + 12}
              textAnchor="middle"
              fontSize={9}
              fontFamily="var(--font-cond)"
              letterSpacing="0.05em"
              fill={held ? "var(--critical)" : regulated ? "var(--warning)" : "var(--faint)"}
            >
              {Math.round(t.speedKmh)} km/h
            </text>
            {/* A decision the controller has taken is called out on the train
                itself, so the map shows what was actually ordered (#23). */}
            {action && (
              <g transform={`translate(${p.x + radius + 8} ${p.y - 4})`}>
                <rect
                  x={0}
                  y={-9}
                  width={action.length * 6.2 + 10}
                  height={16}
                  rx={2}
                  fill="var(--ok)"
                  fillOpacity={0.16}
                  stroke="var(--ok)"
                  strokeWidth={1}
                />
                <text
                  x={5}
                  y={3}
                  fontSize={9.5}
                  fontFamily="var(--font-cond)"
                  letterSpacing="0.08em"
                  fill="var(--ok)"
                >
                  {action}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}

function Legend() {
  return (
    <div className="pointer-events-none absolute right-3 bottom-3 flex flex-col gap-1 border border-border bg-panel/85 px-2 py-1.5">
      {CLASS_LEGEND.map(({ cls, label }) => (
        <div key={cls} className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: CLASS_COLOUR[cls] }}
          />
          <span className="label-xs">{label}</span>
        </div>
      ))}
    </div>
  );
}
