import type {
  Point,
  ResolutionAction,
  ScenarioDefinition,
  ScenarioId,
  Train,
  TrainState,
} from "@/domain/types";
import { crossingDistance } from "./geometry";
import { routeById } from "./topology";

function startS(routeId: string, at: Point): number {
  const r = routeById[routeId]!;
  return crossingDistance(r.path, at, 80) ?? 0;
}

export interface FleetEntry extends Train {
  start: Point;
  nominalSpeedKmh: number;
  /** Alternative routes the optimiser may consider for this train. */
  alternateRouteIds: string[];
  /** Platforms this train could physically be re-assigned to. */
  alternatePlatformIds: string[];
}

export const fleet: FleetEntry[] = [
  {
    id: "E-12928",
    number: "12928",
    name: "Surat — Panvel Express",
    type: "EXPRESS",
    priority: 1,
    routeId: "RT-EXP-DIVA",
    origin: "Surat",
    destination: "Panvel via Diva",
    entryDelayMin: 4,
    start: { x: 1020, y: 620 },
    nominalSpeedKmh: 80,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "F-4271",
    number: "4271",
    name: "Goods — BCN rake",
    type: "FREIGHT",
    priority: 5,
    routeId: "RT-FRT-DIVA",
    origin: "Vasai Road Goods",
    destination: "Kalyan via Diva",
    entryDelayMin: 12,
    start: { x: 930, y: 735 },
    nominalSpeedKmh: 40,
    alternateRouteIds: ["RT-FRT-LOOP"],
    alternatePlatformIds: [],
  },
  {
    id: "M-90701",
    number: "90701",
    name: "Diva — Vasai Road MEMU",
    type: "MEMU",
    priority: 3,
    routeId: "RT-MEMU-IN",
    origin: "Diva Jn",
    destination: "Virar",
    entryDelayMin: 2,
    start: { x: 600, y: 690 },
    nominalSpeedKmh: 50,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "L-90312",
    number: "90312",
    name: "Virar — Churchgate Slow",
    type: "LOCAL",
    priority: 2,
    routeId: "RT-SLOW-DN",
    origin: "Virar",
    destination: "Churchgate",
    entryDelayMin: 1,
    start: { x: 1450, y: 210 },
    nominalSpeedKmh: 65,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "L-90455",
    number: "90455",
    name: "Churchgate — Virar Slow",
    type: "LOCAL",
    priority: 2,
    routeId: "RT-SLOW-UP",
    origin: "Churchgate",
    destination: "Virar",
    entryDelayMin: 0,
    start: { x: 180, y: 283 },
    nominalSpeedKmh: 65,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "K-91007",
    number: "91007",
    name: "Dahanu Road — Churchgate Fast",
    type: "LOCAL",
    priority: 2,
    routeId: "RT-FAST-DN",
    origin: "Dahanu Road",
    destination: "Churchgate",
    entryDelayMin: 3,
    start: { x: 1300, y: 353 },
    nominalSpeedKmh: 85,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "K-91120",
    number: "91120",
    name: "Churchgate — Dahanu Road Fast",
    type: "LOCAL",
    priority: 2,
    routeId: "RT-FAST-UP",
    origin: "Churchgate",
    destination: "Dahanu Road",
    entryDelayMin: 0,
    start: { x: 90, y: 426 },
    nominalSpeedKmh: 85,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "T-12951",
    number: "12951",
    name: "Surat — Mumbai Central Express",
    type: "EXPRESS",
    priority: 1,
    routeId: "RT-THRU-DN",
    origin: "Surat",
    destination: "Mumbai Central",
    entryDelayMin: 0,
    start: { x: 1560, y: 496 },
    nominalSpeedKmh: 95,
    alternateRouteIds: [],
    alternatePlatformIds: ["PF3"],
  },
  {
    id: "T-19024",
    number: "19024",
    name: "Mumbai Central — Surat Express",
    type: "PASSENGER",
    priority: 2,
    routeId: "RT-THRU-UP",
    origin: "Mumbai Central",
    destination: "Surat",
    entryDelayMin: 6,
    start: { x: 60, y: 545 },
    nominalSpeedKmh: 90,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
  {
    id: "S-VR01",
    number: "VR01",
    name: "North yard shunt",
    type: "SHUNT",
    priority: 6,
    routeId: "RT-SHUNT",
    origin: "North yard",
    destination: "North yard",
    entryDelayMin: 0,
    start: { x: 1480, y: 110 },
    nominalSpeedKmh: 15,
    alternateRouteIds: [],
    alternatePlatformIds: [],
  },
];

export const fleetById = Object.fromEntries(fleet.map((f) => [f.id, f])) as Record<
  string,
  FleetEntry
>;

export const scenarios: ScenarioDefinition[] = [
  {
    id: "BASE",
    label: "Base demo scenario",
    description:
      "Express 12928 works PF 6 then the Diva branch while goods 4271 runs the north chord to the same turnout.",
  },
  {
    id: "FREIGHT_DELAY",
    label: "Freight running late",
    description: "Goods 4271 loses time on the chord and arrives into the turnout window later.",
  },
  {
    id: "EXPRESS_DELAY",
    label: "Express running late",
    description: "Express 12928 enters 6 minutes down and holds PF 6 longer.",
  },
  {
    id: "PLATFORM_UNAVAILABLE",
    label: "PF 6 unavailable",
    description: "PF 6 withdrawn from service; Diva-bound expresses must be re-planned.",
  },
  {
    id: "TRACK_BLOCKAGE",
    label: "Branch block BR-1 blocked",
    description: "Diva-bound branch block taken out of service by a permanent-way team.",
  },
  {
    id: "SIGNAL_FAILURE",
    label: "Signal failure at JB",
    description: "Turnout worked under caution; separation over JB doubles.",
  },
  {
    id: "TRAIN_BREAKDOWN",
    label: "Goods brake defect",
    description: "Goods 4271 restricted to 20 km/h on the chord.",
  },
  {
    id: "YARD_CONGESTION",
    label: "Goods yard congestion",
    description: "Goods loop occupied; the loop alternative is not available.",
  },
  {
    id: "PEAK_TRAFFIC",
    label: "Peak suburban traffic",
    description: "Suburban services closed up on the slow and fast pairs.",
  },
  {
    id: "MULTIPLE_DELAYS",
    label: "Multiple simultaneous delays",
    description: "Express late, goods restricted and PF 6 under pressure at once.",
  },
];

export interface ScenarioSetup {
  /** Per-train overrides applied on top of the fleet defaults. */
  overrides: Record<string, Partial<TrainState> & { startShift?: number }>;
  blockedResources: string[];
  /** Route ids that must not be offered as alternatives. */
  unavailableRoutes: string[];
  headwayMultiplier: number;
}

export function scenarioSetup(id: ScenarioId): ScenarioSetup {
  const base: ScenarioSetup = {
    overrides: {},
    blockedResources: [],
    unavailableRoutes: [],
    headwayMultiplier: 1,
  };
  switch (id) {
    case "FREIGHT_DELAY":
      return { ...base, overrides: { "F-4271": { startShift: 150, speedKmh: 32 } } };
    case "EXPRESS_DELAY":
      return { ...base, overrides: { "E-12928": { startShift: -120, speedKmh: 62 } } };
    case "PLATFORM_UNAVAILABLE":
      return { ...base, blockedResources: ["PF6"] };
    case "TRACK_BLOCKAGE":
      return { ...base, blockedResources: ["BLK-DV1"] };
    case "SIGNAL_FAILURE":
      return { ...base, headwayMultiplier: 2 };
    case "TRAIN_BREAKDOWN":
      return {
        ...base,
        overrides: { "F-4271": { speedKmh: 20, nominalSpeedKmh: 40 } },
      };
    case "YARD_CONGESTION":
      return { ...base, unavailableRoutes: ["RT-FRT-LOOP"] };
    case "PEAK_TRAFFIC":
      return {
        ...base,
        overrides: {
          "L-90312": { startShift: 60 },
          "L-90455": { startShift: 90 },
          "K-91007": { startShift: -40 },
        },
      };
    case "MULTIPLE_DELAYS":
      return {
        ...base,
        overrides: {
          "E-12928": { startShift: -100, speedKmh: 60 },
          "F-4271": { speedKmh: 26, nominalSpeedKmh: 40 },
        },
        headwayMultiplier: 1.5,
      };
    default:
      return base;
  }
}

export function initialTrainStates(id: ScenarioId): Record<string, TrainState> {
  const setup = scenarioSetup(id);
  const out: Record<string, TrainState> = {};
  for (const f of fleet) {
    const ov = setup.overrides[f.id] ?? {};
    const s = Math.max(0, startS(f.routeId, f.start) + (ov.startShift ?? 0));
    out[f.id] = {
      trainId: f.id,
      s,
      speedKmh: ov.speedKmh ?? f.nominalSpeedKmh,
      nominalSpeedKmh: ov.nominalSpeedKmh ?? f.nominalSpeedKmh,
      state: "RUNNING",
      dwellRemainingSec: 0,
      nextStopIndex: 0,
      holdRemainingSec: 0,
      delaySec: f.entryDelayMin * 60,
      finished: false,
    };
  }
  return out;
}

export const NO_ACTIONS: ResolutionAction[] = [];
