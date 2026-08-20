/**
 * The console's contract with the backend twin.
 *
 * Position is CHAINAGE — a corridor and a distance in metres from the centre of
 * the Vasai Road platform line. Pixels exist in exactly one place, the
 * projection in `src/twin/projection.ts`, and nowhere else. That separation is
 * why distances on screen can be compressed for legibility while every ETA,
 * headway and separation stays a real physical quantity.
 */

export type CorridorId = "NORTH" | "SOUTH" | "DIVA" | "YARD" | "STATION";

/** A point on the railway, in metres. */
export interface Chainage {
  corridor: CorridorId | string;
  m: number;
}

export interface ScreenPoint {
  x: number;
  y: number;
}

export type ServiceCategory =
  | "EXPRESS" | "PASSENGER" | "LOCAL" | "MEMU" | "FREIGHT" | "SHUNT";

export type ServiceClass =
  | "PREMIUM" | "SUPERFAST" | "MAIL_EXPRESS" | "PASSENGER" | "SUBURBAN"
  | "LOCAL_FAST" | "LOCAL_SEMIFAST" | "LOCAL_SLOW" | "LOCAL_AC"
  | "FREIGHT" | "PREMIUM_FREIGHT" | "FREIGHT_EMPTY" | "SHUNT";

export type TrainState =
  | "SCHEDULED" | "APPROACHING" | "RUNNING" | "REGULATED"
  | "HELD" | "DWELL" | "CLEARED";

export interface DelayBreakdown {
  entry: number;
  dwell: number;
  block_wait: number;
  junction_wait: number;
  platform_wait: number;
  headway_wait: number;
  event: number;
  hold: number;
  regulation: number;
  total: number;
}

export interface TrainSnapshot {
  trainId: string;
  number: string;
  name: string;
  category: ServiceCategory;
  serviceClass: ServiceClass;
  priority: number;
  typicalLoad: number;
  /** Metres along this train's route. */
  s: number;
  routeLengthM: number;
  at: Chainage | null;
  line: string | null;
  platformId: string | null;
  speedKmh: number;
  lineSpeedKmh: number;
  regulatedKmh: number | null;
  state: TrainState;
  dwellRemainingSec: number;
  holdRemainingSec: number;
  bookedDepSec: number;
  actualDepSec: number | null;
  /** Negative means genuinely early. */
  latenessSec: number;
  delayBreakdown: DelayBreakdown;
  origin: string;
  destination: string;
  arrivalCorridor: string;
  departureCorridor: string;
  provenance: string;
  admitted: boolean;
  finished: boolean;
}

export type ConflictSeverity = "CRITICAL" | "WARNING";

export interface Conflict {
  id: string;
  kind: string;
  severity: ConflictSeverity;
  resourceId: string;
  resourceLabel: string;
  resourceKind: "JUNCTION" | "PLATFORM" | "BLOCK" | string;
  at: Chainage;
  trainA: string;
  trainB: string;
  /** Seconds until the first movement reaches the contended resource. */
  etaSec: number;
  separationSec: number;
  requiredSeparationSec: number;
  probability: number;
  timeToConflictSec: number;
}

export interface Prediction {
  horizonSec: number;
  conflicts: Conflict[];
  paths: Record<string, Chainage[]>;
}

/** `null` means NOT YET MEASURED — the console must say so, not print a zero. */
export interface KPISet {
  activeConflicts: number;
  predictedConflicts: number;
  trainsTracked: number;
  scheduledAhead: number;
  totalLatenessSec: number;
  earlinessSec: number;
  averageLatenessSec: number;
  passengerDelaySec: number;
  freightDelaySec: number;
  passengerMinutes: number;
  throughputPerHour: number | null;
  platformUtilisation: number;
  onTimePercent: number | null;
  departuresMeasured: number;
}

export type ActionKind =
  | "HOLD" | "SPEED_REGULATION" | "PLATFORM_REASSIGNMENT" | "ALTERNATE_ROUTE"
  | "NO_ACTION";

export interface ResolutionAction {
  kind: ActionKind;
  trainId: string;
  speedKmh?: number;
  holdSec?: number;
  routeId?: string;
  platformId?: string;
}

export interface SafetyCheck {
  id: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface SafetyValidation {
  passed: boolean;
  checks: SafetyCheck[];
  mode: "RESOLUTION" | "CONTAINMENT";
}

export interface OptionOutcome {
  id: string;
  letter: string;
  title: string;
  action: ResolutionAction;
  conflictResolved: boolean;
  addedDelaySec: Record<string, number>;
  networkDelaySec: number;
  passengerDelaySec: number;
  freightDelaySec: number;
  /** The primary cost: delay multiplied by the people on board. */
  passengerMinutes: number;
  freightTonneMinutes: number;
  throughputDelta: number;
  infrastructureChange: "NONE" | "LOW" | "MEDIUM";
  safety: SafetyValidation;
  residualConflicts: number;
  feasible: boolean;
  infeasibleReason?: string;
  responseClass: "RESOLUTION" | "CONTAINMENT";
}

export interface CostBreakdown {
  residualConflicts: number;
  passengerMinutes: number;
  freightTonneMinutes: number;
  holdSec: number;
  routeChanges: number;
  throughputDelta: number;
  total: number;
}

export interface Alternative {
  title: string;
  passengerMinutes: number;
  networkDelaySec: number;
}

export interface Recommendation {
  mode: "RESOLUTION" | "CONTAINMENT" | "MONITORING";
  status: string;
  conflictId: string | null;
  optionId: string | null;
  rationale: string;
  expectedOutcome: string;
  alternatives: Alternative[];
  costBreakdown?: CostBreakdown;
}

export interface GlobalPlan {
  id: string;
  status: "MONITORING" | "COMPLETE" | "PARTIAL" | "NO_SAFE_PLAN";
  solver: "CP-SAT" | "AMCC" | "NONE";
  solverStatus: string;
  optimalityGap: number | null;
  solveMs: number;
  actions: ResolutionAction[];
  clearedConflicts: string[];
  residualConflicts: string[];
  passengerMinutes: number;
  fcfsPassengerMinutes: number;
  passengerMinutesSaved: number;
  rationale: string;
}

export interface DecisionRecord {
  id: string;
  simTimeSec: number;
  serviceSeconds: number;
  conflictId: string | null;
  where: string;
  trains: string[];
  action: ResolutionAction;
  optionTitle: string;
  note: string;
  outcome: "ACCEPTED" | "MODIFIED" | "REJECTED";
  reason?: string;
  safety?: SafetyValidation;
  latenessBeforeSec?: number;
  latenessAfterSec?: number;
  conflictCleared?: boolean;
}

export interface DelayAvoided {
  decisionsApplied: number;
  decisionsRejected: number;
  /** Hold time issued but not yet served - the comparison is mid-flight. */
  holdInFlightSec: number;
  settling: boolean;
  vsDoNothingSec: number;
  vsPriorityRuleSec: number;
  vsDoNothingPassengerMinutes: number;
  measured: boolean;
}

export interface TrendPoint {
  simTimeSec: number;
  serviceSeconds: number;
  ai: number;
  doNothing: number;
  priorityRule: number;
  aiPassengerMinutes: number;
  doNothingPassengerMinutes: number;
}

export interface MLPrediction {
  target: "ETA" | "DELAY" | "CONFLICT";
  value: number;
  expectedErrorSec?: number;
  intervalLow?: number;
  intervalHigh?: number;
  confidence: number;
  status: "OK" | "LOW_CONFIDENCE";
  modelVersion: string;
  contributions: { feature: string; value: number; contribution: number }[];
}

export interface CausalLink {
  cause_type: string;
  cause_entity: string;
  affected_train: string;
  resource: string;
  added_delay_seconds: number;
  timestamp: number;
}

export interface LiveObservation {
  number: string;
  latenessSec: number;
  lastStation: string;
  observedAt: string;
  source: "live" | "replay" | "cache";
}

export interface LiveDataStatus {
  mode: "off" | "replay" | "live";
  enabled: boolean;
  observedTrains: number;
  observations: LiveObservation[];
  lastPollAt: number | null;
  freightNote: string;
}

export interface Provenance {
  network: string;
  timetable: string;
  timetableServices: number;
  freight: string;
  freightPaths: number;
  freightNote: string;
  timetableCoverage: string[];
}

// ------------------------------------------------------------------ network
export interface NetworkCorridor {
  id: string;
  label: string;
  shortLabel: string;
  screenDir: "LEFT" | "RIGHT" | "UP" | "DOWN_LEFT";
  reachM: number;
  lineSpeedKmh: number;
  stations: { code: string; name: string; chainageM: number }[];
}

export interface NetworkLine {
  id: string;
  label: string;
  kind: string;
  direction: string;
  platformId: string | null;
  speedLimitKmh: number;
}

export interface NetworkPlatform {
  id: string;
  label: string;
  /** SIDE, or ISLAND-A / ISLAND-B / ISLAND-C as tagged in OSM (2;3, 4;5, 6;7). */
  side: string;
  usage: string;
  serves: string[];
  lengthM: number;
}

export interface NetworkResource {
  id: string;
  label: string;
  kind: "JUNCTION" | "PLATFORM" | "BLOCK";
  corridor: string;
  lines: string[];
  fromM: number;
  toM: number;
  centreM: number;
  lengthM: number;
  headwaySec: number;
  capacity: number;
}

export interface GeoPoint {
  lat: number;
  lng: number;
}

export interface LocalPoint {
  x: number;
  y: number;
}

/** Real track geometry from OpenStreetMap, for the geographic map view. */
export interface RailGeometry {
  available: boolean;
  source?: string;
  licence?: string;
  station?: { code: string; name: string; lat: number; lng: number };
  localFrame?: { originLat: number; originLng: number };
  ways: {
    id: number;
    kind: string;
    name: string | null;
    lengthM: number;
    local: LocalPoint[];
    points: GeoPoint[];
  }[];
  platforms: {
    ref: string | null;
    faces: string[];
    lengthM: number;
    local: LocalPoint[];
    points: GeoPoint[];
  }[];
  switches: { id: number; lat: number; lng: number; local: LocalPoint }[];
  signals: { id: number; lat: number; lng: number; local: LocalPoint }[];
}

export interface RailNetwork {
  units: "metres";
  geo?: RailGeometry;
  datum: string;
  stationLimitM: number;
  modelledReachM: number;
  corridors: Record<string, NetworkCorridor>;
  corridorLines: Record<string, string[]>;
  lines: NetworkLine[];
  platforms: NetworkPlatform[];
  resources: NetworkResource[];
}

export type ConnectionStatus =
  | "CONNECTED" | "CONNECTING" | "RECONNECTING" | "OFFLINE";

export type ScenarioId =
  | "BASE" | "PLATFORM_BLOCKED" | "BRANCH_BLOCKED"
  | "SIGNAL_DEGRADED" | "FREIGHT_LATE" | "PEAK_SURGE";

export interface ScenarioDefinition {
  id: ScenarioId;
  label: string;
  description: string;
  mechanism: string;
}

/** One live frame from the backend. */
export interface TwinBundle {
  type: "snapshot";
  connectionStatus: string;
  playing: boolean;
  speed: number;
  simState: {
    simTimeSec: number;
    serviceSeconds: number;
    epochStartMs: number;
    trains: Record<string, TrainSnapshot>;
    scenario: ScenarioId;
    blockedResources: string[];
    headwayMultiplier: number;
    appliedActions: ResolutionAction[];
  };
  prediction: Prediction;
  horizonOffsetSec: number;
  /** The SAME twin projected forward, when the controller scrubs ahead. */
  projected: {
    offsetSec: number;
    trains: Record<string, { trainId: string; s: number; at: Chainage; line: string | null; latenessSec: number }>;
    conflicts: Conflict[];
  } | null;
  kpis: KPISet;
  baselines: { doNothing: KPISet; priorityRule: KPISet; ai: KPISet };
  delayAvoided: DelayAvoided;
  trend: TrendPoint[];
  decisions: DecisionRecord[];
  causalChain: CausalLink[];
  delayBuckets: Record<string, DelayBreakdown>;
  provenance: Provenance;
  liveData: LiveDataStatus;
  clockMode: "LIVE" | "DEMO";
  wallClockMs: number;
  serviceSeconds: number;
  serviceDate: string;
  suggestionRevision: number;
  lastDecisionStatus: { status: string; reason?: string };
  /** Result of checking a controller's MODIFIED action, before it is applied. */
  proposedValidation: {
    conflictId: string | null;
    action?: ResolutionAction;
    passed: boolean;
    clears: boolean;
    reason: string;
    checks: SafetyCheck[];
  } | null;
  modelStatus: {
    optimizer: { status: string; reason: string };
    ml: { status: string; reason: string };
  };
  scenario: ScenarioId;
  optionsByConflict: Record<string, OptionOutcome[]>;
  recommendationByConflict: Record<string, Recommendation | null>;
  options: OptionOutcome[];
  recommendation: Recommendation | null;
  globalPlan: GlobalPlan;
  mlByConflict?: Record<string, MLPrediction>;
  mlByTrain?: Record<string, MLPrediction[]>;
}
