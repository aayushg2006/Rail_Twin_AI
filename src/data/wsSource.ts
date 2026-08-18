/**
 * WebSocket data source — the console's live link to the Python backend.
 *
 * When connected, the backend is authoritative: it streams a full frame (state +
 * prediction + conflicts + options + recommendation + KPIs + causal chain + ML)
 * ~4x/second and the store renders it directly. If the socket cannot be reached
 * (no backend running), this transparently falls back to the in-browser
 * MockTwinSource so the console still works offline — the demo never dies.
 */
import type { ClockMode, CustomScenario, DecisionOutcome, Conflict, ResolutionAction, ScenarioId } from "@/domain/types";
import type { SimState } from "@/twin/engine";
import { MockTwinSource } from "./source";
import type { TwinBundle, TwinDataSource } from "./source";

const RECONNECT_MS = 2000;
const FALLBACK_AFTER_MS = 2500;

export class WebSocketTwinSource implements TwinDataSource {
  readonly kind = "WEBSOCKET" as const;
  private ws: WebSocket | null = null;
  private url: string;
  private state: SimState;
  private bundle: TwinBundle | null = null;
  private listeners = new Set<(s: SimState) => void>();
  private connected = false;
  private reconnecting = false;
  private everConnected = false;
  private usingFallback = false;
  private fallback: MockTwinSource;
  private fallbackTimer: number | null = null;
  private playing = true;
  private speed = 5;

  constructor(url: string, scenario: ScenarioId = "BASE", epochStartMs = Date.now()) {
    this.url = url;
    this.fallback = new MockTwinSource(scenario, epochStartMs);
    this.state = this.fallback.getState();
    this.fallback.subscribe((s) => {
      if (this.usingFallback) {
        this.state = s;
        this.emit();
      }
    });
    this.connect();
    // If we never hear from the backend, switch to the offline mock.
    this.fallbackTimer = window.setTimeout(() => {
      if (!this.everConnected) this.enableFallback();
    }, FALLBACK_AFTER_MS);
  }

  private connect() {
    this.reconnecting = this.everConnected;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.enableFallback();
      return;
    }
    this.ws.onopen = () => {
      this.connected = true;
      this.reconnecting = false;
      this.everConnected = true;
      this.usingFallback = false;
      if (this.fallbackTimer) window.clearTimeout(this.fallbackTimer);
      // sync current UI intent to the authoritative clock
      this.send({ cmd: "set_speed", speed: this.speed });
      this.send({ cmd: this.playing ? "resume" : "pause" });
    };
    this.ws.onmessage = (ev) => {
      let msg: TwinBundle & { type?: string };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!isValidBundle(msg)) return;
      this.bundle = msg;
      this.state = msg.simState;
      this.emit();
    };
    this.ws.onclose = () => {
      this.connected = false;
      if (!this.everConnected) return; // fallback timer handles first-connect failure
      this.reconnecting = true;
      // reconnect; keep last frame on screen meanwhile
      window.setTimeout(() => this.connect(), RECONNECT_MS);
    };
    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private enableFallback() {
    if (this.usingFallback) return;
    this.usingFallback = true;
    this.bundle = null;
    this.state = this.fallback.getState();
    this.emit();
  }

  private send(obj: unknown) {
    if (this.ws && this.connected) this.ws.send(JSON.stringify(obj));
  }

  private emit() {
    for (const l of this.listeners) l(this.state);
  }

  getState() {
    return this.state;
  }

  getBundle() {
    return this.connected ? this.bundle : null;
  }

  subscribe(listener: (s: SimState) => void) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  advance(dtSec: number) {
    // Backend drives the clock when connected; only the offline mock ticks here.
    if (this.usingFallback) this.fallback.advance(dtSec);
  }

  seek(simTimeSec: number) {
    if (this.usingFallback) {
      this.fallback.seek(simTimeSec);
      return;
    }
    this.send({ cmd: "seek", simTimeSec });
  }

  applyAction(action: ResolutionAction) {
    if (this.usingFallback) {
      this.fallback.applyAction(action);
      return;
    }
    this.send({ cmd: "apply_action", action });
  }

  decide(conflict: Conflict, action: ResolutionAction, outcome: DecisionOutcome, note: string, expectedRevision?: number) {
    if (this.usingFallback) {
      if (outcome !== "REJECTED") this.fallback.applyAction(action);
      return;
    }
    this.send({ cmd: "decide", conflictId: conflict.id, action, outcome, note, expectedRevision });
  }

  loadScenario(scenario: ScenarioId) {
    if (this.usingFallback) {
      this.fallback.loadScenario(scenario);
      return;
    }
    this.send({ cmd: "load_scenario", scenario });
  }

  loadCustomScenario(scenario: CustomScenario) {
    if (this.usingFallback) {
      this.fallback.loadCustomScenario(scenario);
      return;
    }
    this.send({ cmd: "load_custom_scenario", scenario });
  }

  setPlaying(v: boolean) {
    this.playing = v;
    if (this.usingFallback) return;
    this.send({ cmd: v ? "resume" : "pause" });
  }

  setSpeed(v: number) {
    this.speed = v;
    if (this.usingFallback) return;
    this.send({ cmd: "set_speed", speed: v });
  }

  setClockMode(mode: ClockMode) {
    if (this.usingFallback) return;
    this.send({ cmd: "set_clock_mode", mode });
  }

  connectionState() {
    return this.connected ? "CONNECTED" : this.usingFallback ? "SIMULATED" : this.reconnecting ? "RECONNECTING" : "OFFLINE";
  }
}

function isValidBundle(value: unknown): value is TwinBundle & { type?: string } {
  if (!value || typeof value !== "object") return false;
  const frame = value as Partial<TwinBundle>;
  return !!frame.simState && typeof frame.simState === "object"
    && !!frame.prediction && Array.isArray(frame.prediction.conflicts)
    && !!frame.kpis && typeof frame.kpis === "object";
}
