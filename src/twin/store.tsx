import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type {
  CausalLink,
  Conflict,
  CustomScenario,
  DecisionOutcome,
  DecisionRecord,
  DelayBreakdown,
  KPISet,
  MLPrediction,
  OptionOutcome,
  Point,
  Prediction,
  Recommendation,
  GlobalPlan,
  ResolutionAction,
  ScenarioId,
  ClockMode,
  ConnectionStatus,
} from "@/domain/types";
import { MockTwinSource, type TwinBundle, type TwinDataSource } from "@/data/source";
import { WebSocketTwinSource } from "@/data/wsSource";
import {
  applyAction,
  computeKPIs,
  generateOptions,
  predict,
  projectStateAt,
  recommend,
  type SimState,
} from "./engine";
import { clockOf } from "./format";

export type SimSpeed = 1 | 2 | 5 | 10 | 20;
export type ConnectionState = ConnectionStatus;

/** Pick the authoritative source: the Python backend over WebSocket when
 *  reachable, else the in-browser deterministic mock (offline fallback). */
function createSource(scenario: ScenarioId, epochStartMs: number): TwinDataSource {
  if (typeof window === "undefined") return new MockTwinSource(scenario, epochStartMs);
  const env = (import.meta as { env?: Record<string, string> }).env ?? {};
  const url = env["VITE_BACKEND_WS_URL"] ?? `ws://${window.location.hostname}:8000/ws`;
  if (!url) return new MockTwinSource(scenario, epochStartMs);
  return new WebSocketTwinSource(url, scenario, epochStartMs);
}

export interface LayerFlags {
  infrastructure: boolean;
  live: boolean;
  predicted: boolean;
  decision: boolean;
}

/** What the contextual inspector is currently describing. */
export type Selection =
  | { kind: "train"; id: string }
  | { kind: "platform"; id: string }
  | { kind: "track"; id: string }
  | { kind: "conflict"; id: string }
  | null;

interface TwinContextValue {
  /** Authoritative live state. */
  sim: SimState;
  /** State rendered on the map — live, or projected when scrubbing. */
  view: SimState;
  prediction: Prediction;
  kpis: KPISet;
  options: OptionOutcome[];
  recommendation: Recommendation | null;
  globalPlan: GlobalPlan | null;
  selectedConflict: Conflict | null;
  selectedTrainId: string | null;
  selection: Selection;
  /** Non-involved network elements are de-emphasised while true. */
  focusMode: boolean;
  /** Option currently being inspected / simulated by the controller. */
  previewOption: OptionOutcome | null;
  /** Projected path of the previewed option's train, for the recommended layer. */
  previewPath: Point[] | null;
  horizonOffset: number;
  playing: boolean;
  speed: SimSpeed;
  layers: LayerFlags;
  scenario: ScenarioId;
  decisions: DecisionRecord[];
  baselineKpis: KPISet | null;
  /** Cumulative delay a naive controller would have added vs. the AI's decisions. */
  delayAvoidedSec: number | null;
  /** True when the current window has no active trains (advance the clock). */
  emptyNetwork: boolean;
  emptyNetworkReason: string | null;
  /** Live link state to the backend brain. */
  connection: ConnectionState;
  /** Computed delay-dependency chain (backend), for the propagation panel. */
  causalChain: CausalLink[];
  /** Per-train per-cause delay breakdown (backend), for explainability. */
  delayBuckets: Record<string, DelayBreakdown>;
  /** ML predictions keyed by conflict id (backend). */
  mlByConflict: Record<string, MLPrediction>;
  /** ML predictions keyed by train id (backend). */
  mlByTrain: Record<string, MLPrediction[]>;
  suggestionRevision: number | null;
  clockMode: ClockMode;
  decisionStatus: { status: string; reason?: string } | null;
  setPlaying: (v: boolean) => void;
  setSpeed: (v: SimSpeed) => void;
  setClockMode: (mode: ClockMode) => void;
  setHorizonOffset: (v: number) => void;
  selectTrain: (id: string | null) => void;
  selectConflict: (id: string | null) => void;
  select: (sel: Selection) => void;
  setFocusMode: (v: boolean) => void;
  setPreviewOptionId: (id: string | null) => void;
  toggleLayer: (k: keyof LayerFlags) => void;
  loadScenario: (id: ScenarioId) => void;
  loadCustomScenario: (scenario: CustomScenario) => void;
  stepForward: (sec: number) => void;
  jumpToNextEvent: () => void;
  decide: (
    option: OptionOutcome,
    outcome: DecisionOutcome,
    note?: string,
    override?: ResolutionAction,
  ) => void;
}

const TwinContext = createContext<TwinContextValue | null>(null);

const TICK_MS = 250;

export function TwinProvider({ children }: { children: ReactNode }) {
  const sourceRef = useRef<TwinDataSource | null>(null);
  if (!sourceRef.current)
    sourceRef.current = createSource("BASE", Date.now());
  const source = sourceRef.current;

  const [sim, setSim] = useState<SimState>(() => source.getState());
  const [bundle, setBundle] = useState<TwinBundle | null>(() => source.getBundle?.() ?? null);
  const [playing, setPlayingState] = useState(true);
  const [speed, setSpeedState] = useState<SimSpeed>(5);
  const [horizonOffset, setHorizonOffset] = useState(0);
  const [selectedTrainId, setSelectedTrainId] = useState<string | null>(null);
  const [selectedConflictId, setSelectedConflictId] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [focusMode, setFocusMode] = useState(false);
  const [previewOptionId, setPreviewOptionId] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [baselineKpis, setBaselineKpis] = useState<KPISet | null>(null);
  const [layers, setLayers] = useState<LayerFlags>({
    infrastructure: true,
    live: true,
    predicted: true,
    decision: true,
  });

  useEffect(
    () =>
      source.subscribe((s) => {
        setSim(s);
        setBundle(source.getBundle?.() ?? null);
      }),
    [source],
  );

  // Keep the authoritative clock's play/speed intent in sync with the UI.
  const setPlaying = useCallback(
    (v: boolean) => {
      setPlayingState(v);
      source.setPlaying?.(v);
    },
    [source],
  );
  const setSpeed = useCallback(
    (v: SimSpeed) => {
      setSpeedState(v);
      source.setSpeed?.(v);
    },
    [source],
  );

  useEffect(() => {
    if (!playing) return;
    // When the backend is authoritative, advance() is a no-op (the backend
    // drives the clock); only the offline mock ticks locally here.
    const id = window.setInterval(() => {
      source.advance((TICK_MS / 1000) * speed);
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [playing, speed, source]);

  // Predictions are recomputed on a coarse cadence so the horizon is stable
  // while trains animate smoothly.
  const predictionKey = Math.floor(sim.simTimeSec / 2);
  const localPrediction = useMemo(
    () => predict(sim),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [predictionKey, sim.scenario, sim.appliedActions.length, sim.blockedResources.join()],
  );
  // Backend-computed prediction wins when connected; otherwise derive locally.
  const prediction = bundle?.prediction ?? localPrediction;

  const localKpis = useMemo(() => computeKPIs(sim, prediction), [sim, prediction]);
  const kpis = bundle?.kpis ?? localKpis;

  useEffect(() => {
    setBaselineKpis((prev) => prev ?? kpis);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim.scenario]);

  const selectedConflict = useMemo(() => {
    if (prediction.conflicts.length === 0) return null;
    return (
      prediction.conflicts.find((c) => c.id === selectedConflictId) ?? prediction.conflicts[0]!
    );
  }, [prediction, selectedConflictId]);

  const optionsKey = `${selectedConflict?.id ?? ""}|${Math.floor(sim.simTimeSec / 10)}|${sim.appliedActions.length}`;
  const localOptions = useMemo(
    () => (selectedConflict ? generateOptions(sim, selectedConflict) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [optionsKey],
  );
  // Backend (OR-Tools) options win when present for the selected conflict.
  const backendOptions =
    selectedConflict && bundle
      ? (bundle.optionsByConflict?.[selectedConflict.id] ?? bundle.options)
      : undefined;
  const options = backendOptions ?? localOptions;

  const localRecommendation = useMemo(
    () => (selectedConflict ? recommend(selectedConflict, localOptions) : null),
    [selectedConflict, localOptions],
  );
  const backendRecommendation =
    selectedConflict && bundle
      ? (bundle.recommendationByConflict?.[selectedConflict.id] ?? bundle.recommendation)
      : undefined;
  const recommendation =
    backendRecommendation !== undefined ? backendRecommendation : localRecommendation;

  const view = useMemo(
    () => (horizonOffset > 0 ? projectStateAt(sim, horizonOffset) : sim),
    [sim, horizonOffset],
  );

  const previewOption = useMemo(() => {
    if (options.length === 0) return null;
    return (
      options.find((o) => o.id === previewOptionId) ??
      options.find((o) => o.id === recommendation?.optionId) ??
      null
    );
  }, [options, previewOptionId, recommendation]);

  /** Projected trajectory of the previewed action, drawn as the recommended layer. */
  const previewPathKey = `${previewOption?.id ?? ""}|${optionsKey}`;
  const previewPath = useMemo(() => {
    if (!previewOption || !previewOption.feasible) return null;
    const applied = applyAction(sim, previewOption.action);
    return predict(applied).paths[previewOption.action.trainId] ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewPathKey]);

  const decide = useCallback(
    (
      option: OptionOutcome,
      outcome: DecisionOutcome,
      note = "",
      override?: ResolutionAction,
    ) => {
      const conflict = selectedConflict;
      if (!conflict) return;
      const action = override ?? option.action;
      const record: DecisionRecord = {
        id: `D-${Date.now()}`,
        simTimeSec: sim.simTimeSec,
        wallClock: clockOf(sim.epochStartMs + sim.simTimeSec * 1000),
        conflictId: conflict.id,
        conflictLabel: `${conflict.trainA}${conflict.trainB ? ` / ${conflict.trainB}` : ""} @ ${conflict.resourceId}`,
        optionTitle: option.title,
        action,
        outcome,
        networkDelaySec: option.networkDelaySec,
        note,
        kpiBefore: kpis,
      };
      setDecisions((d) => [record, ...d]);
      // Prefer the backend decision path (safety re-validation + audit log);
      // fall back to a direct action apply for the offline mock.
      if (source.decide) {
        source.decide(conflict, action, outcome, note, bundle?.suggestionRevision);
      } else if (outcome !== "REJECTED") {
        source.applyAction(action);
      }
      if (outcome !== "REJECTED") setHorizonOffset(0);
    },
    [selectedConflict, sim, kpis, source],
  );


  const stepForward = useCallback(
    (sec: number) => {
      setPlaying(false);
      source.seek(source.getState().simTimeSec + sec);
    },
    [source],
  );

  const jumpToNextEvent = useCallback(() => {
    const next = prediction.conflicts[0];
    const target = next ? Math.max(15, next.etaSec - 45) : 120;
    setPlaying(false);
    source.seek(source.getState().simTimeSec + target);
  }, [prediction, source]);

  const loadScenario = useCallback(
    (id: ScenarioId) => {
      source.loadScenario(id);
      setSelectedConflictId(null);
      setSelectedTrainId(null);
      setSelection(null);
      setFocusMode(false);
      setPreviewOptionId(null);
      setHorizonOffset(0);
      setBaselineKpis(null);
      setPlaying(true);
    },
    [source],
  );

  const setClockMode = useCallback((mode: ClockMode) => {
    source.setClockMode?.(mode);
  }, [source]);

  const loadCustomScenario = useCallback(
    (scenario: CustomScenario) => {
      source.loadCustomScenario?.(scenario);
      setSelectedConflictId(null);
      setPreviewOptionId(null);
      setHorizonOffset(0);
      setBaselineKpis(null);
      setPlaying(true);
    },
    [source],
  );

  const toggleLayer = useCallback((k: keyof LayerFlags) => {
    setLayers((l) => ({ ...l, [k]: !l[k] }));
  }, []);

  const selectTrain = useCallback((id: string | null) => {
    setSelectedTrainId(id);
    setSelection(id ? { kind: "train", id } : null);
  }, []);

  const selectConflict = useCallback((id: string | null) => {
    setSelectedConflictId(id);
    setSelection(id ? { kind: "conflict", id } : null);
    setPreviewOptionId(null);
  }, []);

  const select = useCallback((sel: Selection) => {
    setSelection(sel);
    if (sel?.kind === "train") setSelectedTrainId(sel.id);
    else if (sel?.kind === "conflict") setSelectedConflictId(sel.id);
    else if (!sel) setSelectedTrainId(null);
  }, []);

  const value: TwinContextValue = {
    sim,
    view,
    prediction,
    kpis,
    options,
    recommendation,
    globalPlan: bundle?.globalPlan ?? null,
    selectedConflict,
    selectedTrainId,
    selection,
    focusMode,
    previewOption,
    previewPath,
    horizonOffset,
    playing,
    speed,
    layers,
    scenario: sim.scenario,
    decisions,
    baselineKpis: bundle?.baselineKpis ?? baselineKpis,
    delayAvoidedSec: bundle?.delayAvoidedSec ?? null,
    emptyNetwork: bundle?.emptyNetwork ?? false,
    emptyNetworkReason: bundle?.emptyNetworkReason ?? null,
    connection: source.connectionState(),
    causalChain: bundle?.causalChain ?? [],
    delayBuckets: bundle?.delayBuckets ?? {},
    mlByConflict: bundle?.mlByConflict ?? {},
    mlByTrain: bundle?.mlByTrain ?? {},
    suggestionRevision: bundle?.suggestionRevision ?? null,
    clockMode: bundle?.clockMode ?? sim.clockMode,
    decisionStatus: bundle?.lastDecisionStatus ?? null,
    setPlaying,
    setSpeed,
    setClockMode,
    setHorizonOffset,
    selectTrain,
    selectConflict,
    select,
    setFocusMode,
    setPreviewOptionId,
    toggleLayer,
    loadScenario,
    loadCustomScenario,
    stepForward,
    jumpToNextEvent,
    decide,
  };

  return <TwinContext.Provider value={value}>{children}</TwinContext.Provider>;
}

export function useTwin(): TwinContextValue {
  const ctx = useContext(TwinContext);
  if (!ctx) throw new Error("useTwin must be used inside TwinProvider");
  return ctx;
}
