import rawData from "../../data/vasai-data.json";
import type {
  DataPack,
  Point,
  ResolutionAction,
  ScenarioDefinition,
  ScenarioId,
  Train,
  TrainState,
} from "@/domain/types";
import { crossingDistance, kmhToUnitsPerSec, pathLength } from "./geometry";
import { routeById } from "./topology";

export const dataPack = rawData as unknown as DataPack;

function startS(routeId: string, at: Point): number {
  const r = routeById[routeId]!;
  return crossingDistance(r.path, at, 80) ?? 0;
}

export interface FleetEntry extends Train {
  start: Point;
  nominalSpeedKmh: number;
  alternateRouteIds: string[];
  alternatePlatformIds: string[];
  operatingDays: string;
  snapshotDate: string;
}

export const fleet: FleetEntry[] = dataPack.services.map((service) => ({
  ...service,
  alternateRouteIds: [...(service.alternateRouteIds ?? [])],
  alternatePlatformIds: [...(service.alternatePlatformIds ?? [])],
}));

export const fleetById = Object.fromEntries(fleet.map((f) => [f.id, f])) as Record<
  string,
  FleetEntry
>;

export const scenarios: ScenarioDefinition[] = dataPack.scenarios;

export interface ScenarioSetup {
  overrides: Record<string, Partial<TrainState> & { startShift?: number }>;
  blockedResources: string[];
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
      return { ...base, overrides: { "F-4271": { speedKmh: 20, nominalSpeedKmh: 40 } } };
    case "YARD_CONGESTION":
      return { ...base, unavailableRoutes: ["RT-FRT-LOOP"] };
    case "PEAK_TRAFFIC":
      return {
        ...base,
        overrides: {
          "L-93009": { startShift: 60 },
          "L-93008": { startShift: 90 },
          "L-93011": { startShift: -40 },
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

function secondsSinceMidnight(epochMs: number): number {
  const d = new Date(epochMs);
  const utc = d.getUTCHours() * 3600 + d.getUTCMinutes() * 60 + d.getUTCSeconds();
  return (utc + 5.5 * 3600) % 86400;
}

export function initialTrainStates(id: ScenarioId, epochStartMs = Date.now()): Record<string, TrainState> {
  const setup = scenarioSetup(id);
  const out: Record<string, TrainState> = {};
  const nowSec = secondsSinceMidnight(epochStartMs);
  for (const f of fleet) {
    const ov = setup.overrides[f.id] ?? {};
    const s = Math.max(0, startS(f.routeId, f.start) + (ov.startShift ?? 0));
    const departure = f.scheduledDepartureSec ?? 0;
    const activationAtSec = departure > nowSec ? departure - nowSec : 0;
    const scheduled = activationAtSec > 0;
    const route = routeById[f.routeId]!;
    const nominalDuration = pathLength(route.path) / Math.max(1, kmhToUnitsPerSec(f.nominalSpeedKmh))
      + route.stops.reduce((sum, stop) => sum + stop.dwellSec, 0);
    const completedAtStart = nowSec >= departure && nowSec - departure > nominalDuration + 300;
    out[f.id] = {
      trainId: f.id,
      s,
      speedKmh: ov.speedKmh ?? f.nominalSpeedKmh,
      nominalSpeedKmh: ov.nominalSpeedKmh ?? f.nominalSpeedKmh,
      state: completedAtStart ? "CLEARED" : scheduled ? "SCHEDULED" : "RUNNING",
      dwellRemainingSec: 0,
      nextStopIndex: 0,
      holdRemainingSec: 0,
      delaySec: f.entryDelayMin * 60,
      scheduledDepartureSec: departure,
      activationAtSec,
      latenessSec: f.entryDelayMin * 60,
      earlinessSec: 0,
      signedVarianceSec: f.entryDelayMin * 60,
      finished: completedAtStart,
    };
  }
  return out;
}

export const NO_ACTIONS: ResolutionAction[] = [];
