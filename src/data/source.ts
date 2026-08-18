/**
 * Data layer boundary.
 *
 * The console never talks to the simulation engine directly — it subscribes to
 * a TwinDataSource. The prototype ships MockTwinSource, which runs the
 * deterministic engine in the browser. A FastAPI + WebSocket source can be
 * dropped in behind the same interface without touching any component.
 */
import type {
  CausalLink,
  CustomScenario,
  Conflict,
  DecisionOutcome,
  DelayBreakdown,
  KPISet,
  MLPrediction,
  OptionOutcome,
  Prediction,
  Recommendation,
  ResolutionAction,
  ScenarioId,
  ClockMode,
  ConnectionStatus,
  GlobalPlan,
} from "@/domain/types";
import { advanceTo, applyAction, createSimState, tick, type SimState } from "@/twin/engine";

/**
 * Everything the authoritative backend computes for one live frame. When a
 * WebSocket source is connected the store renders these directly (the brain is
 * the Python backend); the mock source leaves it null and the console derives
 * the same values locally as an offline fallback.
 */
export interface TwinBundle {
  connection: ConnectionStatus;
  connectionStatus?: ConnectionStatus;
  simState: SimState;
  prediction: Prediction;
  kpis: KPISet;
  baselineKpis?: KPISet | null;
  /** Cumulative delay (s) a naive controller would have added vs. the AI's choices. */
  delayAvoidedSec?: number;
  /** True when no trains are active in the current window. */
  emptyNetwork?: boolean;
  emptyNetworkReason?: string;
  options?: OptionOutcome[];
  optionsByConflict?: Record<string, OptionOutcome[]>;
  recommendation?: Recommendation | null;
  recommendationByConflict?: Record<string, Recommendation | null>;
  globalPlan?: GlobalPlan | null;
  globalPlanStatus?: string;
  causalChain?: CausalLink[];
  delayBuckets?: Record<string, DelayBreakdown>;
  mlByTrain?: Record<string, MLPrediction[]>;
  mlByConflict?: Record<string, MLPrediction>;
  playing?: boolean;
  speed?: number;
  dataPackId?: string;
  dataProvenance?: string;
  snapshotDate?: string;
  suggestionRevision?: number;
  suggestionGeneratedAt?: number;
  wallClockMs?: number;
  clockMode?: ClockMode;
  serviceDate?: string;
  persistenceStatus?: string;
  lastDecisionStatus?: { status: string; reason?: string };
}

export interface TwinDataSource {
  readonly kind: "MOCK" | "WEBSOCKET";
  getState(): SimState;
  subscribe(listener: (state: SimState) => void): () => void;
  /** Advance the twin by `dt` simulation seconds. */
  advance(dtSec: number): void;
  /** Jump the twin forward to a specific simulation time. */
  seek(simTimeSec: number): void;
  applyAction(action: ResolutionAction): void;
  loadScenario(scenario: ScenarioId): void;
  loadCustomScenario?(scenario: CustomScenario): void;
  connectionState(): ConnectionStatus;
  /** Backend-computed frame, or null when the source derives locally. */
  getBundle?(): TwinBundle | null;
  /** Play/pause the authoritative clock (no-op for the local mock). */
  setPlaying?(v: boolean): void;
  /** Set the authoritative clock speed (no-op for the local mock). */
  setSpeed?(v: number): void;
  setClockMode?(mode: ClockMode): void;
  /** Record a controller decision on the backend (accept/modify/reject). */
  decide?(
    conflict: Conflict,
    action: ResolutionAction,
    outcome: DecisionOutcome,
    note: string,
    expectedRevision?: number,
  ): void;
}

export class MockTwinSource implements TwinDataSource {
  readonly kind = "MOCK" as const;
  private state: SimState;
  private listeners = new Set<(s: SimState) => void>();

  constructor(scenario: ScenarioId = "BASE", epochStartMs = Date.now()) {
    this.state = createSimState(scenario, epochStartMs);
  }

  getState() {
    return this.state;
  }

  subscribe(listener: (s: SimState) => void) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit() {
    for (const l of this.listeners) l(this.state);
  }

  advance(dtSec: number) {
    if (dtSec <= 0) return;
    this.state = tick(this.state, dtSec);
    this.emit();
  }

  seek(simTimeSec: number) {
    if (simTimeSec <= this.state.simTimeSec) return;
    this.state = advanceTo(this.state, simTimeSec, 2);
    this.emit();
  }

  applyAction(action: ResolutionAction) {
    this.state = applyAction(this.state, action);
    this.emit();
  }

  loadScenario(scenario: ScenarioId) {
    this.state = createSimState(scenario, this.state.epochStartMs);
    this.emit();
  }

  loadCustomScenario(scenario: CustomScenario) {
    this.state = createSimState("BASE", this.state.epochStartMs);
    this.state.scenario = scenario.id;
    for (const event of scenario.events) {
      if (event.kind === "BLOCK" || event.kind === "PLATFORM") {
        this.state.blockedResources = [...new Set([...this.state.blockedResources, event.targetId])];
      } else if (event.kind === "HEADWAY") {
        this.state.headwayMultiplier = Math.max(0.5, Math.min(4, event.value ?? 1));
      } else if (event.kind === "SPEED_RESTRICTION" || event.kind === "BREAKDOWN") {
        this.state = applyAction(this.state, {
          kind: "SPEED_REGULATION",
          trainId: event.targetId,
          speedKmh: event.value ?? 20,
        });
      } else if (event.kind === "TRAIN_DELAY" || event.kind === "HOLD") {
        this.state = applyAction(this.state, {
          kind: "HOLD",
          trainId: event.targetId,
          holdSec: event.value ?? 0,
        });
      }
    }
    this.emit();
  }

  connectionState() {
    return "SIMULATED" as const;
  }
}
