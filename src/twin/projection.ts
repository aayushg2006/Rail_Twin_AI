/**
 * Chainage -> screen. The ONLY place in the console where a pixel exists.
 *
 * The physics is metric: a train is at (corridor, metres). A schematic is not a
 * scale drawing, so each corridor gets its own metres-per-pixel — the station
 * area is drawn generously and the approaches are compressed — but that choice
 * affects nothing except what the eye sees. Previously the drawing WAS the
 * model, which is how the Diva branch ended up 17.4 km long on a map that
 * labelled it 36.8.
 */
import type { Chainage, RailNetwork, ScreenPoint } from "@/domain/types";

export const VIEW_W = 1640;
export const VIEW_H = 940;

/** Station centre. Chainage 0 on every corridor lands here. */
const CX = 820;

/** Half-width of the drawn station box. */
const STATION_HALF_PX = 300;

/** Pixels available to each approach beyond the station box. */
const APPROACH_PX = 470;

/**
 * Running lines, top to bottom, with the y they sit at. The Diva branch peels
 * away below the through roads; the goods chord and yard sit below that, well
 * clear of the passenger roads so the junction throat stays readable.
 */
export const LINE_Y = {
  SLD: 150,
  SLU: 220,
  FSD: 290,
  FSU: 360,
  THD: 430,
  THU: 500,
  BRU: 585,
  BRD: 645,
  GDC: 730,
  GYL: 820,
} as const;

export type LineId = keyof typeof LINE_Y;

const DEFAULT_Y = LINE_Y.THD;

/** y for a running line, falling back to the through road for anything new. */
export function lineY(id: string | null | undefined): number {
  return (LINE_Y as Record<string, number | undefined>)[id ?? ""] ?? DEFAULT_Y;
}

export const PLATFORM_Y = {
  PF1: LINE_Y.SLD,
  PF2: LINE_Y.SLU,
  PF3: LINE_Y.FSD,
  PF4: LINE_Y.FSU,
  PF5: LINE_Y.THD,
  PF6: LINE_Y.THU,
  PF7: LINE_Y.BRU,
} as const;

/** y for a platform face, falling back to its line. */
export function platformY(id: string, lineId?: string | null): number {
  return (PLATFORM_Y as Record<string, number | undefined>)[id] ?? lineY(lineId);
}

/** Where the branch and the goods chord leave the station box, in metres. */
const BRANCH_TURNOUT_M = 900;
const CHORD_TURNOUT_M = 2100;

/** How far down-left the branch runs once clear of the throat. */
const BRANCH_DROP_PX = 190;

export interface Projector {
  network: RailNetwork;
  /** Screen point for a chainage on a given running line. */
  point(at: Chainage, lineId?: string | null): ScreenPoint;
  /** Polyline for the drawn extent of one running line. */
  linePath(lineId: string): ScreenPoint[];
  /** Screen span of a resource on a line, as [start, end]. */
  resourceSpan(resourceId: string, lineId?: string | null): [ScreenPoint, ScreenPoint] | null;
  stationBox(): { x: number; y: number; w: number; h: number };
  corridorReach(corridor: string): number;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Chainage -> x. Inside the station limit the scale is generous; beyond it the
 * approach is compressed into a fixed pixel budget so 8 km still fits on screen.
 */
function axialX(corridor: string, m: number, stationLimitM: number, reachM: number): number {
  const dir = corridor === "SOUTH" ? -1 : 1;
  const inside = Math.min(Math.abs(m), stationLimitM);
  const beyond = Math.max(0, Math.abs(m) - stationLimitM);
  const insidePx = (inside / stationLimitM) * STATION_HALF_PX;
  const beyondSpan = Math.max(1, reachM - stationLimitM);
  const beyondPx = (beyond / beyondSpan) * APPROACH_PX;
  return CX + dir * (insidePx + beyondPx);
}

export function createProjector(network: RailNetwork): Projector {
  const stationLimitM = network.stationLimitM;
  const resourceById = new Map(network.resources.map((r) => [r.id, r]));

  const corridorReach = (corridor: string): number =>
    network.corridors[corridor]?.reachM ?? network.modelledReachM;

  function branchPoint(m: number): ScreenPoint {
    // The branch leaves the throat at the turnout and falls away to the
    // bottom-left, so the crossing movements are visibly separated rather than
    // all converging on one dot.
    const reach = corridorReach("DIVA");
    if (m <= BRANCH_TURNOUT_M) {
      const t = clamp(m / BRANCH_TURNOUT_M, 0, 1);
      return { x: CX - t * 150, y: LINE_Y.BRD + t * 30 };
    }
    const t = clamp((m - BRANCH_TURNOUT_M) / Math.max(1, reach - BRANCH_TURNOUT_M), 0, 1);
    return {
      x: CX - 150 - t * (APPROACH_PX + 180),
      y: LINE_Y.BRD + 30 + t * BRANCH_DROP_PX,
    };
  }

  function chordPoint(m: number): ScreenPoint {
    const reach = corridorReach("NORTH");
    const t = clamp(m / reach, 0, 1);
    return { x: CX + t * (STATION_HALF_PX + APPROACH_PX), y: LINE_Y.GDC };
  }

  function yardPoint(m: number): ScreenPoint {
    const reach = corridorReach("YARD");
    const t = clamp(m / reach, 0, 1);
    return { x: CX - 120 - t * 420, y: LINE_Y.GYL };
  }

  function point(at: Chainage, lineId?: string | null): ScreenPoint {
    const corridor = at.corridor;
    const line = lineId ?? "";

    if (corridor === "YARD" || line === "GYL") return yardPoint(at.m);
    if (corridor === "DIVA") {
      return line === "GDC"
        ? { x: branchPoint(at.m).x, y: branchPoint(at.m).y + 55 }
        : branchPoint(at.m);
    }
    if (line === "GDC") return chordPoint(at.m);
    if (corridor === "STATION") {
      return { x: CX, y: platformY(line, line) };
    }
    const y = lineY(line);
    return { x: axialX(corridor, at.m, stationLimitM, corridorReach(corridor)), y };
  }

  function linePath(lineId: string): ScreenPoint[] {
    if (lineId === "GYL") {
      return [yardPoint(0), yardPoint(corridorReach("YARD"))];
    }
    if (lineId === "BRD" || lineId === "BRU") {
      const reach = corridorReach("DIVA");
      const offset = lineId === "BRU" ? -28 : 0;
      const steps = [0, BRANCH_TURNOUT_M, reach * 0.45, reach];
      return steps.map((m) => {
        const p = branchPoint(m);
        return { x: p.x, y: p.y + offset };
      });
    }
    if (lineId === "GDC") {
      const reach = corridorReach("NORTH");
      const branch = branchPoint(corridorReach("DIVA"));
      return [
        { x: branch.x, y: branch.y + 55 },
        { x: CX, y: LINE_Y.GDC },
        chordPoint(reach),
      ];
    }
    // Western main: a straight road across the whole modelled extent.
    const y = lineY(lineId);
    const south = axialX("SOUTH", corridorReach("SOUTH"), stationLimitM, corridorReach("SOUTH"));
    const north = axialX("NORTH", corridorReach("NORTH"), stationLimitM, corridorReach("NORTH"));
    return [
      { x: south, y },
      { x: north, y },
    ];
  }

  function resourceSpan(
    resourceId: string,
    lineId?: string | null,
  ): [ScreenPoint, ScreenPoint] | null {
    const res = resourceById.get(resourceId);
    if (!res) return null;
    const line = lineId ?? res.lines[0] ?? null;
    if (res.kind === "PLATFORM") {
      const y = platformY(res.id, line);
      return [
        { x: CX - STATION_HALF_PX * 0.55, y },
        { x: CX + STATION_HALF_PX * 0.55, y },
      ];
    }
    return [
      point({ corridor: res.corridor, m: res.fromM }, line),
      point({ corridor: res.corridor, m: res.toM }, line),
    ];
  }

  return {
    network,
    point,
    linePath,
    resourceSpan,
    corridorReach,
    stationBox: () => ({
      x: CX - STATION_HALF_PX,
      y: LINE_Y.SLD - 60,
      w: STATION_HALF_PX * 2,
      h: LINE_Y.BRU - LINE_Y.SLD + 120,
    }),
  };
}

/** Colour per service class — items 5 and 27 both key off this. */
export const CLASS_COLOUR: Record<string, string> = {
  PREMIUM: "var(--premium)",
  SUPERFAST: "var(--express)",
  MAIL_EXPRESS: "var(--express)",
  PASSENGER: "var(--express)",
  SUBURBAN: "var(--memu)",
  LOCAL_FAST: "var(--local-fast)",
  LOCAL_AC: "var(--local-fast)",
  LOCAL_SEMIFAST: "var(--local)",
  LOCAL_SLOW: "var(--local)",
  FREIGHT: "var(--freight)",
  PREMIUM_FREIGHT: "var(--freight-premium)",
  FREIGHT_EMPTY: "var(--freight-empty)",
  SHUNT: "var(--shunt)",
};

export const CLASS_LABEL: Record<string, string> = {
  PREMIUM: "Premium express",
  SUPERFAST: "Superfast",
  MAIL_EXPRESS: "Mail / express",
  PASSENGER: "Passenger",
  SUBURBAN: "MEMU",
  LOCAL_FAST: "Fast local",
  LOCAL_AC: "AC local",
  LOCAL_SEMIFAST: "Semifast local",
  LOCAL_SLOW: "Slow local",
  FREIGHT: "Goods",
  PREMIUM_FREIGHT: "Container / POL",
  FREIGHT_EMPTY: "Goods (empty)",
  SHUNT: "Shunt",
};

/** Legend order for the map key. */
export const CLASS_LEGEND: { cls: string; label: string }[] = [
  { cls: "PREMIUM", label: "Premium" },
  { cls: "SUPERFAST", label: "Express" },
  { cls: "LOCAL_FAST", label: "Fast local" },
  { cls: "LOCAL_SLOW", label: "Slow local" },
  { cls: "SUBURBAN", label: "MEMU" },
  { cls: "FREIGHT", label: "Goods" },
  { cls: "SHUNT", label: "Shunt" },
];
