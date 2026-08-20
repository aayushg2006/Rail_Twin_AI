/**
 * The GEOGRAPHIC view: real OpenStreetMap track geometry, in real coordinates.
 *
 * The schematic in `projection.ts` straightens the railway so a controller can
 * read it at a glance. This one does the opposite — it draws Vasai Road where it
 * actually is, so the console can be held up against RailRadar's own map and
 * agree. Both are fed from the same metric twin; only the drawing differs.
 *
 * OSM supplies each way already projected into metres east/north of the station
 * node (`local`), which is an equirectangular approximation — accurate to
 * centimetres over a 20 km box at 19.4°N, and free of any projection library.
 */
import type {
  Chainage,
  RailGeometry,
  RailNetwork,
  ScreenPoint,
} from "@/domain/types";
import type { Projector } from "./projection";

/**
 * A GeoProjector IS a Projector: the drawing layers (trains, predicted paths,
 * conflict spans) are written once against the interface and work unchanged in
 * either view. Only the infrastructure layer differs, because only that layer
 * cares whether the track is straightened or real.
 */
export interface GeoProjector extends Projector {
  available: boolean;
  geometry: RailGeometry | null;
  /** The whole modelled extent, so nothing is ever clipped. */
  viewBox: { x: number; y: number; w: number; h: number };
  /** Where the view opens: the junction itself, not the whole 16 km. */
  home: { x: number; y: number; w: number; h: number };
  /** Metres east/north of the station node -> screen units. */
  local(x: number, y: number): ScreenPoint;
  /** Real coordinates -> screen units. */
  latLng(lat: number, lng: number): ScreenPoint;
  /** A train's chainage -> screen units, following the real alignment. */
  point(at: Chainage, lineId?: string | null): ScreenPoint;
  platformFor(faceId: string): { local: ScreenPoint[]; lengthM: number } | null;
}

/** Screen units per metre. The station throat is ~1 km across, so this keeps
 *  the whole junction and both approaches on one canvas. */
const SCALE = 0.14;

/**
 * Where each corridor runs, taken from the OSM extent: the Western main runs
 * roughly north-south (bearing ~350°), and the Diva branch leaves the south end
 * heading east-south-east. These are the real bearings, not drawing choices.
 */
const CORRIDOR_BEARING: Record<string, number> = {
  // degrees clockwise from north
  NORTH: 349,
  SOUTH: 169,
  DIVA: 118,
  YARD: 331,
  STATION: 349,
};

/** Lateral offset per running line, in metres, so parallel roads are distinct. */
const LINE_OFFSET_M: Record<string, number> = {
  SLD: -18,
  SLU: -9,
  FSD: 0,
  FSU: 9,
  THD: 18,
  THU: 27,
  BRU: 36,
  BRD: 45,
  GDC: 60,
  GYL: 75,
};

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

export function createGeoProjector(network: RailNetwork): GeoProjector {
  const geometry = network.geo?.available ? network.geo : null;
  const resourceById = new Map(network.resources.map((r) => [r.id, r]));
  const stationLimitM = network.stationLimitM;

  // Fit the view to whatever geometry exists; fall back to a fixed box.
  let minX = -2200;
  let maxX = 2600;
  let minY = -5000;
  let maxY = 7200;
  if (geometry) {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const way of geometry.ways) {
      for (const p of way.local) {
        xs.push(p.x);
        ys.push(p.y);
      }
    }
    if (xs.length > 0) {
      minX = Math.min(...xs);
      maxX = Math.max(...xs);
      minY = Math.min(...ys);
      maxY = Math.max(...ys);
    }
  }

  // The corridors are modelled well past the OSM box (Virar is 8 km out, Diva
  // 36.8), so the drawn extent has to take in the bearing walks too or the
  // approaches get clipped at the edge of the downloaded tiles.
  {
    const reach = (c: string) => network.corridors[c]?.reachM ?? network.modelledReachM;
    for (const [corridor, bearing] of Object.entries(CORRIDOR_BEARING)) {
      const theta = toRad(bearing);
      const m = corridor === "STATION" ? 0 : reach(corridor);
      const x = m * Math.sin(theta);
      const y = m * Math.cos(theta);
      minX = Math.min(minX, x - 120);
      maxX = Math.max(maxX, x + 120);
      minY = Math.min(minY, y - 120);
      maxY = Math.max(maxY, y + 120);
    }
  }

  const pad = 200;
  const local = (x: number, y: number): ScreenPoint => ({
    x: (x - minX + pad) * SCALE,
    // Screen y grows downward; metres north grow upward.
    y: (maxY - y + pad) * SCALE,
  });

  const viewBox = {
    x: 0,
    y: 0,
    w: (maxX - minX + pad * 2) * SCALE,
    h: (maxY - minY + pad * 2) * SCALE,
  };

  /**
   * The console opens on the junction - about 3.5 km across, enough to take in
   * all seven faces, the throat and the branch turnout - and pans out from
   * there. Opening on the full 16 km would make the station a smudge.
   */
  const HOME_HALF_M = 1750;
  const homeCentre = local(200, 0);
  const home = {
    x: homeCentre.x - HOME_HALF_M * SCALE,
    y: homeCentre.y - HOME_HALF_M * SCALE * 0.62,
    w: HOME_HALF_M * 2 * SCALE,
    h: HOME_HALF_M * 2 * SCALE * 0.62,
  };

  const originLat = geometry?.station?.lat ?? 19.3826676;
  const originLng = geometry?.station?.lng ?? 72.8320245;
  const metresPerLat = 111132;
  const metresPerLng = 111320 * Math.cos(toRad(originLat));

  const latLng = (lat: number, lng: number): ScreenPoint =>
    local((lng - originLng) * metresPerLng, (lat - originLat) * metresPerLat);

  /**
   * Chainage is distance along a corridor from the platform line. Projected
   * geographically that is a bearing walk from the station, offset sideways by
   * which running line the movement is on.
   */
  const point = (at: Chainage, lineId?: string | null): ScreenPoint => {
    const bearing = CORRIDOR_BEARING[at.corridor] ?? 349;
    const theta = toRad(bearing);
    const along = at.m;
    const across = LINE_OFFSET_M[lineId ?? ""] ?? 0;
    // Forward along the bearing, then perpendicular for the parallel road.
    const x = along * Math.sin(theta) + across * Math.cos(theta);
    const y = along * Math.cos(theta) - across * Math.sin(theta);
    return local(x, y);
  };

  const corridorReach = (corridor: string): number =>
    network.corridors[corridor]?.reachM ?? network.modelledReachM;

  /** The drawn extent of one running line, walked along its real bearings. */
  const linePath = (lineId: string): ScreenPoint[] => {
    const branch = lineId === "BRD" || lineId === "BRU";
    const yard = lineId === "GYL";
    if (yard) {
      return [
        point({ corridor: "YARD", m: 0 }, lineId),
        point({ corridor: "YARD", m: corridorReach("YARD") }, lineId),
      ];
    }
    if (branch) {
      const reach = corridorReach("DIVA");
      return [0, reach * 0.25, reach * 0.6, reach].map((m) =>
        point({ corridor: "DIVA", m }, lineId),
      );
    }
    if (lineId === "GDC") {
      // The goods chord: off the branch at the south end, round to the north.
      return [
        point({ corridor: "DIVA", m: corridorReach("DIVA") * 0.3 }, lineId),
        point({ corridor: "STATION", m: 0 }, lineId),
        point({ corridor: "NORTH", m: corridorReach("NORTH") }, lineId),
      ];
    }
    return [
      point({ corridor: "SOUTH", m: corridorReach("SOUTH") }, lineId),
      point({ corridor: "STATION", m: 0 }, lineId),
      point({ corridor: "NORTH", m: corridorReach("NORTH") }, lineId),
    ];
  };

  /** Where a resource sits on screen, so conflicts highlight the real track. */
  const resourceSpan = (
    resourceId: string,
    lineId?: string | null,
  ): [ScreenPoint, ScreenPoint] | null => {
    const res = resourceById.get(resourceId);
    if (!res) return null;
    const line = lineId ?? res.lines[0] ?? null;
    if (res.kind === "PLATFORM") {
      const face = platformFor(res.id);
      if (face && face.local.length >= 2) {
        // Draw along the platform's own polygon, end to end.
        const xs = face.local;
        let a = xs[0]!;
        let b = xs[0]!;
        let best = -1;
        for (const p of xs) {
          for (const q of xs) {
            const d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2;
            if (d > best) {
              best = d;
              a = p;
              b = q;
            }
          }
        }
        return [a, b];
      }
      const half = Math.max(120, stationLimitM * 0.35);
      return [
        point({ corridor: "SOUTH", m: half }, line),
        point({ corridor: "NORTH", m: half }, line),
      ];
    }
    return [
      point({ corridor: res.corridor, m: res.fromM }, line),
      point({ corridor: res.corridor, m: res.toM }, line),
    ];
  };

  const stationBox = () => {
    const c = local(0, 0);
    const half = Math.max(60, stationLimitM * SCALE);
    return { x: c.x - half, y: c.y - half * 1.4, w: half * 2, h: half * 2.8 };
  };

  const platformFor = (faceId: string) => {
    const match = geometry?.platforms.find((p) => p.faces.includes(faceId));
    if (!match) return null;
    return {
      local: match.local.map((p) => local(p.x, p.y)),
      lengthM: match.lengthM,
    };
  };

  return {
    network,
    available: !!geometry,
    geometry,
    viewBox,
    home,
    local,
    latLng,
    point,
    linePath,
    resourceSpan,
    corridorReach,
    stationBox,
    platformFor,
  };
}

/** How each OSM way class is drawn. */
export const GEO_WAY_STYLE: Record<
  string,
  { stroke: string; width: number; dash?: string; opacity?: number }
> = {
  WESTERN_MAIN: { stroke: "var(--rail-strong)", width: 2.4 },
  BRANCH_OR_LINK: { stroke: "var(--branch)", width: 2.0 },
  FREIGHT_SIDING: { stroke: "var(--freight)", width: 1.6, dash: "6 4" },
  CROSSOVER: { stroke: "var(--selected)", width: 1.8 },
  YARD: { stroke: "var(--rail)", width: 1.1, dash: "4 4", opacity: 0.75 },
  OTHER: { stroke: "var(--rail)", width: 1.1, opacity: 0.6 },
};
