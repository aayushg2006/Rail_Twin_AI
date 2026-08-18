/**
 * Data layer boundary.
 *
 * The console never talks to the simulation engine directly — it subscribes to
 * a TwinDataSource. The prototype ships MockTwinSource, which runs the
 * deterministic engine in the browser. A FastAPI + WebSocket source can be
 * dropped in behind the same interface without touching any component.
 */
import type { ResolutionAction, ScenarioId } from "@/domain/types";
import { advanceTo, applyAction, createSimState, tick, type SimState } from "@/twin/engine";

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
  connectionState(): "CONNECTED" | "SIMULATED" | "OFFLINE";
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

  connectionState() {
    return "SIMULATED" as const;
  }
}
