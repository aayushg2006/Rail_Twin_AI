/**
 * Vasai Road Digital Twin — domain model.
 *
 * These types describe railway state only. No presentation concerns live here.
 */

export type Point = { x: number; y: number };

export type Direction = "UP" | "DOWN" | "BRANCH_OUT" | "BRANCH_IN";

export type LineKind =
  | "WESTERN_SLOW"
  | "WESTERN_FAST"
  | "DIVA_BRANCH"
  | "FREIGHT_CHORD"
  | "YARD";

export interface Track {
  id: string;
  name: string;
  kind: LineKind;
  direction: Direction;
  /** Ordered polyline in map units. */
  path: Point[];
  /** Long-distance / through line rather than suburban. */
  through?: boolean;
}

export interface Platform {
  id: string;
  label: string;
  /** Rectangle in map units. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Track ids served by this platform face. */
  serves: string[];
  usage: string;
  side: "WEST" | "ISLAND" | "EAST";
}

export type ResourceKind = "JUNCTION" | "PLATFORM" | "BLOCK";

/** A finite piece of infrastructure that trains contend for. */
export interface Resource {
  id: string;
  label: string;
  kind: ResourceKind;
  at: Point;
  /** Capture radius, map units — a route passing within this uses the resource. */
  radius: number;
  /** Minimum separation between two movements over this resource, seconds. */
  headwaySec: number;
  capacity: number;
}

export interface SignalPost {
  id: string;
  at: Point;
  /** Rotation, degrees. 0 = facing right (toward Dahanu Road). */
  facing: number;
  aspect: "GREEN" | "YELLOW" | "RED";
}

export interface Junction {
  id: string;
  label: string;
  at: Point;
}

export interface StationTick {
  name: string;
  at: Point;
  km: number;
  corridor: CorridorId;
}

export type CorridorId = "NORTH" | "SOUTH" | "DIVA" | "STATION";

export interface RouteStop {
  /** Distance along the route path, map units. */
  s: number;
  platformId: string;
  dwellSec: number;
}

export interface RailRoute {
  id: string;
  label: string;
  /** Continuous polyline the train physically follows. */
  path: Point[];
  /** Track ids traversed, in order — used for direction validation. */
  tracks: string[];
  stops: RouteStop[];
  corridorFrom: CorridorId;
  corridorTo: CorridorId;
}

export type TrainType = "EXPRESS" | "PASSENGER" | "LOCAL" | "MEMU" | "FREIGHT" | "SHUNT";

export type TrainOperationalState =
  | "RUNNING"
  | "APPROACHING"
  | "DWELL"
  | "HELD"
  | "REGULATED"
  | "CLEARED";

export interface Train {
  id: string;
  number: string;
  name: string;
  type: TrainType;
  /** 1 = highest operational priority. */
  priority: number;
  routeId: string;
  origin: string;
  destination: string;
  /** Scheduled delay carried on entry, minutes. */
  entryDelayMin: number;
}

/** Mutable live state of one train. */
export interface TrainState {
  trainId: string;
  /** Distance travelled along its route path, map units. */
  s: number;
  /** Commanded line speed, km/h. */
  speedKmh: number;
  /** Line speed before any regulation was applied. */
  nominalSpeedKmh: number;
  state: TrainOperationalState;
  /** Remaining dwell at the current stop, seconds. */
  dwellRemainingSec: number;
  /** Index of the next stop in the route. */
  nextStopIndex: number;
  /** Additional hold imposed by a controller decision, seconds remaining. */
  holdRemainingSec: number;
  /** Accumulated delay, seconds. */
  delaySec: number;
  /** Set once the train has run off the end of its route. */
  finished: boolean;
}

export type ConflictKind =
  | "JUNCTION_CONTENTION"
  | "PLATFORM_OCCUPATION_OVERLAP"
  | "HEADWAY_VIOLATION_RISK"
  | "BLOCK_OCCUPANCY"
  | "ROUTE_CONFLICT";

export type ConflictSeverity = "CRITICAL" | "WARNING";

export interface Conflict {
  id: string;
  kind: ConflictKind;
  severity: ConflictSeverity;
  resourceId: string;
  resourceLabel: string;
  at: Point;
  trainA: string;
  trainB: string;
  /** Simulation seconds from now until the first train reaches the resource. */
  etaSec: number;
  /** Separation between the two movements over the resource, seconds. */
  separationSec: number;
  requiredSeparationSec: number;
}

export interface PredictedSample {
  trainId: string;
  /** Offsets from now, seconds. */
  tSec: number;
  at: Point;
  s: number;
}

export interface Prediction {
  horizonSec: number;
  conflicts: Conflict[];
  /** Projected path polyline per train over the horizon. */
  paths: Record<string, Point[]>;
}

export type ActionKind =
  | "SPEED_REGULATION"
  | "HOLD"
  | "ALTERNATE_ROUTE"
  | "PLATFORM_REASSIGNMENT"
  | "NO_ACTION";

export interface ResolutionAction {
  kind: ActionKind;
  trainId: string;
  /** km/h for SPEED_REGULATION. */
  speedKmh?: number;
  /** seconds for HOLD. */
  holdSec?: number;
  routeId?: string;
  platformId?: string;
}

export interface OptionOutcome {
  id: string;
  letter: string;
  title: string;
  action: ResolutionAction;
  conflictResolved: boolean;
  /** Per-train added delay, seconds, keyed by train id. */
  addedDelaySec: Record<string, number>;
  networkDelaySec: number;
  passengerDelaySec: number;
  freightDelaySec: number;
  throughputDelta: number;
  infrastructureChange: "NONE" | "LOW" | "MEDIUM";
  safety: SafetyValidation;
  /** Conflicts still predicted after applying the action. */
  residualConflicts: number;
  feasible: boolean;
  infeasibleReason?: string;
}

export interface SafetyValidation {
  passed: boolean;
  checks: { id: string; label: string; passed: boolean; detail: string }[];
}

export interface Recommendation {
  conflictId: string;
  optionId: string;
  rationale: string;
  expectedOutcome: string;
  alternatives: string[];
}

export interface KPISet {
  activeConflicts: number;
  trainsTracked: number;
  totalDelaySec: number;
  averageDelaySec: number;
  passengerDelaySec: number;
  freightDelaySec: number;
  throughputPerHour: number | null;
  platformUtilisation: number;
  onTimePercent: number;
  recoveryTimeSec: number | null;
}

export type DecisionOutcome = "ACCEPTED" | "REJECTED" | "MODIFIED";

export interface DecisionRecord {
  id: string;
  simTimeSec: number;
  wallClock: string;
  conflictId: string;
  conflictLabel: string;
  optionTitle: string;
  action: ResolutionAction;
  outcome: DecisionOutcome;
  networkDelaySec: number;
  note: string;
  kpiBefore: KPISet;
}

export type ScenarioId =
  | "BASE"
  | "FREIGHT_DELAY"
  | "EXPRESS_DELAY"
  | "PLATFORM_UNAVAILABLE"
  | "TRACK_BLOCKAGE"
  | "SIGNAL_FAILURE"
  | "TRAIN_BREAKDOWN"
  | "YARD_CONGESTION"
  | "PEAK_TRAFFIC"
  | "MULTIPLE_DELAYS";

export interface ScenarioDefinition {
  id: ScenarioId;
  label: string;
  description: string;
}

export interface TwinSnapshot {
  simTimeSec: number;
  /** Epoch millis of the authoritative simulation clock. */
  clockMs: number;
  lastUpdateMs: number;
  trainStates: Record<string, TrainState>;
  /** Platform id -> occupying train id. */
  platformOccupancy: Record<string, string | null>;
  /** Resource id -> blocked flag (disruption). */
  blockedResources: string[];
  scenario: ScenarioId;
  appliedActions: ResolutionAction[];
}
