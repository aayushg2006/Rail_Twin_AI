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
  Conflict,
  ConnectionStatus,
  DecisionRecord,
  OptionOutcome,
  RailNetwork,
  Recommendation,
  ResolutionAction,
  ScenarioId,
  TrainSnapshot,
  TwinBundle,
} from "@/domain/types";
import { TwinSocket } from "@/data/wsSource";
import { createProjector, type Projector } from "./projection";

export type SimSpeed = 1 | 2 | 5 | 10;

export type Selection =
  | { kind: "train"; id: string }
  | { kind: "platform"; id: string }
  | { kind: "conflict"; id: string }
  | null;

export interface LayerFlags {
  infrastructure: boolean;
  live: boolean;
  predicted: boolean;
  decision: boolean;
}

interface TwinContextValue {
  bundle: TwinBundle | null;
  network: RailNetwork | null;
  projector: Projector | null;
  connection: ConnectionStatus;
  /** Trains actually on the ground — scheduled ones never reach the map (#22). */
  activeTrains: TrainSnapshot[];
  conflicts: Conflict[];
  /** True when the map is showing a projected frame rather than now. */
  projecting: boolean;
  selectedConflict: Conflict | null;
  options: OptionOutcome[];
  recommendation: Recommendation | null;
  decisions: DecisionRecord[];
  selection: Selection;
  selectedTrainId: string | null;
  focusMode: boolean;
  whatIfOpen: boolean;
  previewOption: OptionOutcome | null;
  horizonOffset: number;
  playing: boolean;
  speed: SimSpeed;
  layers: LayerFlags;
  scenario: ScenarioId;
  select: (sel: Selection) => void;
  selectTrain: (id: string | null) => void;
  selectConflict: (id: string | null) => void;
  setFocusMode: (v: boolean) => void;
  setPreviewOptionId: (id: string | null) => void;
  openWhatIf: (id?: string) => void;
  closeWhatIf: () => void;
  setHorizonOffset: (v: number) => void;
  setPlaying: (v: boolean) => void;
  setSpeed: (v: SimSpeed) => void;
  toggleLayer: (k: keyof LayerFlags) => void;
  loadScenario: (id: ScenarioId) => void;
  resetToBase: () => void;
  decide: (
    option: OptionOutcome,
    outcome: "ACCEPTED" | "MODIFIED" | "REJECTED",
    note?: string,
    override?: ResolutionAction,
  ) => void;
}

const TwinContext = createContext<TwinContextValue | null>(null);

function backendUrls(): { ws: string; http: string } {
  const env = (import.meta as { env?: Record<string, string> }).env ?? {};
  const host = typeof window === "undefined" ? "localhost" : window.location.hostname;
  const ws = env["VITE_BACKEND_WS_URL"] ?? `ws://${host}:8000/ws`;
  return { ws, http: ws.replace(/^ws/, "http").replace(/\/ws$/, "") };
}

export function TwinProvider({ children }: { children: ReactNode }) {
  // This console renders live data that only exists in the browser. Server
  // rendering it produces a tree the client can never match, so wait for mount
  // rather than hydrating a placeholder and reconciling against real data.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const socketRef = useRef<TwinSocket | null>(null);
  const [bundle, setBundle] = useState<TwinBundle | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("CONNECTING");
  const [network, setNetwork] = useState<RailNetwork | null>(null);

  const [selection, setSelection] = useState<Selection>(null);
  const [selectedTrainId, setSelectedTrainId] = useState<string | null>(null);
  const [selectedConflictId, setSelectedConflictId] = useState<string | null>(null);
  const [focusMode, setFocusMode] = useState(false);
  const [whatIfOpen, setWhatIfOpen] = useState(false);
  const [previewOptionId, setPreviewOptionId] = useState<string | null>(null);
  const [horizonOffset, setHorizonOffset] = useState(0);
  const [playing, setPlayingState] = useState(true);
  const [speed, setSpeedState] = useState<SimSpeed>(5);
  const [layers, setLayers] = useState<LayerFlags>({
    infrastructure: true,
    live: true,
    predicted: true,
    decision: true,
  });

  // The network is static; fetch it once and build the projector from it.
  useEffect(() => {
    const { http } = backendUrls();
    let cancelled = false;
    const load = () =>
      fetch(`${http}/api/network`)
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((n: RailNetwork) => {
          if (!cancelled) setNetwork(n);
        })
        .catch(() => window.setTimeout(load, 2000));
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const { ws } = backendUrls();
    const socket = new TwinSocket(ws, setBundle, setConnection);
    socketRef.current = socket;
    socket.connect();
    return () => socket.close();
  }, []);

  const projector = useMemo(
    () => (network ? createProjector(network) : null),
    [network],
  );

  // Only admitted, unfinished trains are on the ground. Scheduled services are
  // future bookings and must never be drawn as if they were running (#22).
  const activeTrains = useMemo(() => {
    const trains = bundle?.simState.trains ?? {};
    const live = Object.values(trains).filter((t) => t.admitted && !t.finished);
    const ahead = horizonOffset > 0 ? bundle?.projected?.trains : undefined;
    const view = ahead
      ? live.flatMap((t) => {
          const p = ahead[t.trainId];
          return p ? [{ ...t, s: p.s, at: p.at, line: p.line, latenessSec: p.latenessSec }] : [];
        })
      : live;
    return view.sort(
      (a, b) => a.priority - b.priority || a.number.localeCompare(b.number),
    );
  }, [bundle, horizonOffset]);

  // While scrubbing, the console shows the BACKEND'S projected frame. It no
  // longer runs a second model whose answer could differ from the numbers
  // printed next to it.
  const projected = horizonOffset > 0 ? (bundle?.projected ?? null) : null;
  const conflicts = projected?.conflicts ?? bundle?.prediction.conflicts ?? [];

  const selectedConflict = useMemo(
    () => conflicts.find((c) => c.id === selectedConflictId) ?? null,
    [conflicts, selectedConflictId],
  );

  const options = useMemo(() => {
    if (!bundle || !selectedConflict) return [];
    return bundle.optionsByConflict?.[selectedConflict.id] ?? [];
  }, [bundle, selectedConflict]);

  const recommendation = useMemo(() => {
    if (!bundle) return null;
    if (!selectedConflict) return bundle.recommendation;
    return bundle.recommendationByConflict?.[selectedConflict.id] ?? null;
  }, [bundle, selectedConflict]);

  const previewOption = useMemo(() => {
    if (!options.length) return null;
    return (
      options.find((o) => o.id === previewOptionId) ??
      options.find((o) => o.id === recommendation?.optionId) ??
      null
    );
  }, [options, previewOptionId, recommendation]);

  // A conflict that has been resolved or expired must not leave a stale modal.
  useEffect(() => {
    if (selectedConflictId && !conflicts.some((c) => c.id === selectedConflictId)) {
      setSelectedConflictId(null);
      setWhatIfOpen(false);
      setPreviewOptionId(null);
    }
  }, [conflicts, selectedConflictId]);

  const send = useCallback((msg: Record<string, unknown>) => {
    socketRef.current?.send(msg);
  }, []);

  const setPlaying = useCallback(
    (v: boolean) => {
      setPlayingState(v);
      send({ cmd: v ? "resume" : "pause" });
    },
    [send],
  );

  const setSpeed = useCallback(
    (v: SimSpeed) => {
      setSpeedState(v);
      send({ cmd: "set_speed", speed: v });
    },
    [send],
  );

  const openWhatIf = useCallback(
    (id?: string) => {
      const target = id ?? selectedConflictId ?? conflicts[0]?.id;
      if (!target) return;
      setSelectedConflictId(target);
      setPreviewOptionId(null);
      setWhatIfOpen(true);
    },
    [conflicts, selectedConflictId],
  );

  const closeWhatIf = useCallback(() => {
    setWhatIfOpen(false);
    setPreviewOptionId(null);
  }, []);

  const selectConflict = useCallback(
    (id: string | null) => {
      setSelectedConflictId(id);
      setSelection(id ? { kind: "conflict", id } : null);
      setPreviewOptionId(null);
      if (id) setWhatIfOpen(true);
      else setWhatIfOpen(false);
    },
    [],
  );

  const selectTrain = useCallback((id: string | null) => {
    setSelectedTrainId(id);
    setSelection(id ? { kind: "train", id } : null);
  }, []);

  const select = useCallback((sel: Selection) => {
    setSelection(sel);
    setSelectedTrainId(sel?.kind === "train" ? sel.id : null);
    if (sel?.kind === "conflict") {
      setSelectedConflictId(sel.id);
      setWhatIfOpen(true);
    }
  }, []);

  const decide = useCallback(
    (
      option: OptionOutcome,
      outcome: "ACCEPTED" | "MODIFIED" | "REJECTED",
      note = "",
      override?: ResolutionAction,
    ) => {
      if (!selectedConflict) return;
      // The decision log is written by the backend after re-validation, so what
      // it records is what actually happened - not what the UI hoped would.
      send({
        cmd: "decide",
        conflictId: selectedConflict.id,
        action: override ?? option.action,
        outcome,
        note,
        optionTitle: override ? `${option.title} (modified)` : option.title,
        expectedRevision: bundle?.suggestionRevision,
        responseMode: option.responseClass,
      });
      setWhatIfOpen(false);
      setSelectedConflictId(null);
      setPreviewOptionId(null);
      setHorizonOffset(0);
    },
    [bundle, selectedConflict, send],
  );

  const loadScenario = useCallback(
    (id: ScenarioId) => {
      send({ cmd: "load_scenario", scenario: id });
      setSelectedConflictId(null);
      setWhatIfOpen(false);
      setSelectedTrainId(null);
      setSelection(null);
      setPreviewOptionId(null);
      setHorizonOffset(0);
      setPlayingState(true);
    },
    [send],
  );

  const resetToBase = useCallback(() => {
    send({ cmd: "reset" });
    setSelectedConflictId(null);
    setWhatIfOpen(false);
    setSelection(null);
    setHorizonOffset(0);
    setPlayingState(true);
  }, [send]);

  const toggleLayer = useCallback((k: keyof LayerFlags) => {
    setLayers((l) => ({ ...l, [k]: !l[k] }));
  }, []);

  const setHorizon = useCallback(
    (v: number) => {
      setHorizonOffset(v);
      // Scrubbing asks the BACKEND for the projected frame; the console no
      // longer runs a second model of its own that could disagree with it.
      send({ cmd: "set_horizon_offset", offsetSec: v });
    },
    [send],
  );

  const value: TwinContextValue = {
    bundle,
    network,
    projector,
    connection,
    activeTrains,
    conflicts,
    projecting: !!projected,
    selectedConflict,
    options,
    recommendation,
    decisions: bundle?.decisions ?? [],
    selection,
    selectedTrainId,
    focusMode,
    whatIfOpen,
    previewOption,
    horizonOffset,
    playing,
    speed,
    layers,
    scenario: bundle?.scenario ?? "BASE",
    select,
    selectTrain,
    selectConflict,
    setFocusMode,
    setPreviewOptionId,
    openWhatIf,
    closeWhatIf,
    setHorizonOffset: setHorizon,
    setPlaying,
    setSpeed,
    toggleLayer,
    loadScenario,
    resetToBase,
    decide,
  };

  if (!mounted) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-shell">
        <p className="label-xs text-muted-foreground">Connecting to the twin…</p>
      </div>
    );
  }

  return <TwinContext.Provider value={value}>{children}</TwinContext.Provider>;
}

export function useTwin(): TwinContextValue {
  const ctx = useContext(TwinContext);
  if (!ctx) throw new Error("useTwin must be used inside TwinProvider");
  return ctx;
}
