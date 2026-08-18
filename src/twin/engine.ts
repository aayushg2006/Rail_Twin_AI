/**
 * Deterministic simulation engine for the Vasai Road digital twin.
 *
 * Everything the interface shows — positions, ETAs, conflicts, option
 * outcomes, KPIs — is produced here from one authoritative state object.
 * Nothing is randomised and no result is hard-coded.
 */
import type {
  Conflict,
  ConflictSeverity,
  KPISet,
  OptionOutcome,
  Point,
  Prediction,
  RailRoute,
  Recommendation,
  ResolutionAction,
  SafetyValidation,
  ScenarioId,
  TrainState,
} from "@/domain/types";
import { crossingDistance, kmhToUnitsPerSec, pathLength, pathSlice, pointAt } from "./geometry";
import { resourceById, routeById, routeResourceUse } from "./topology";
import { fleetById, fleet, initialTrainStates, scenarioSetup } from "./scenario";

export const DEFAULT_HORIZON_SEC = 900;
export const RESPAWN_GAP_SEC = 150;

export interface SimState {
  simTimeSec: number;
  /** Wall-clock epoch millis the simulation clock started from. */
  epochStartMs: number;
  lastUpdateSec: number;
  trains: Record<string, TrainState>;
  scenario: ScenarioId;
  /** trainId -> routeId when a controller decision re-routed a train. */
  routeOverrides: Record<string, string>;
  blockedResources: string[];
  headwayMultiplier: number;
  unavailableRoutes: string[];
  appliedActions: ResolutionAction[];
  /** trainId -> sim time it may re-enter the section. */
  respawnAt: Record<string, number>;
}

export function createSimState(scenario: ScenarioId, epochStartMs: number): SimState {
  const setup = scenarioSetup(scenario);
  return {
    simTimeSec: 0,
    epochStartMs,
    lastUpdateSec: 0,
    trains: initialTrainStates(scenario),
    scenario,
    routeOverrides: {},
    blockedResources: setup.blockedResources,
    headwayMultiplier: setup.headwayMultiplier,
    unavailableRoutes: setup.unavailableRoutes,
    appliedActions: [],
    respawnAt: {},
  };
}

export function cloneState(s: SimState): SimState {
  return {
    ...s,
    trains: Object.fromEntries(Object.entries(s.trains).map(([k, v]) => [k, { ...v }])),
    routeOverrides: { ...s.routeOverrides },
    blockedResources: [...s.blockedResources],
    unavailableRoutes: [...s.unavailableRoutes],
    appliedActions: [...s.appliedActions],
    respawnAt: { ...s.respawnAt },
  };
}

export function routeFor(state: SimState, trainId: string): RailRoute {
  const id = state.routeOverrides[trainId] ?? fleetById[trainId]!.routeId;
  return routeById[id]!;
}

const routeLengthCache = new Map<string, number>();
export function routeLength(route: RailRoute): number {
  const cached = routeLengthCache.get(route.id);
  if (cached !== undefined) return cached;
  const len = pathLength(route.path);
  routeLengthCache.set(route.id, len);
  return len;
}

export function trainPoint(state: SimState, trainId: string): Point {
  return pointAt(routeFor(state, trainId).path, state.trains[trainId]!.s);
}

/** Seconds until the train reaches distance `sTarget`, or null if unreachable. */
export function projectArrival(state: SimState, trainId: string, sTarget: number): number | null {
  const st = state.trains[trainId];
  if (!st || st.finished) return null;
  const route = routeFor(state, trainId);
  if (sTarget < st.s - 1) return null;
  const v = kmhToUnitsPerSec(Math.max(1, st.speedKmh));
  let t = st.holdRemainingSec + st.dwellRemainingSec;
  let d = st.s;
  for (let i = st.nextStopIndex; i < route.stops.length; i++) {
    const stop = route.stops[i]!;
    if (stop.s > sTarget) break;
    if (stop.s <= d) continue;
    t += (stop.s - d) / v;
    d = stop.s;
    t += stop.dwellSec;
  }
  t += Math.max(0, sTarget - d) / v;
  return t;
}

export function projectFinish(state: SimState, trainId: string): number | null {
  const route = routeFor(state, trainId);
  return projectArrival(state, trainId, routeLength(route));
}

/** Advance the twin by `dt` simulation seconds. Mutates a fresh clone. */
export function tick(prev: SimState, dt: number): SimState {
  const state = cloneState(prev);
  state.simTimeSec += dt;
  state.lastUpdateSec = state.simTimeSec;

  for (const f of fleet) {
    const st = state.trains[f.id]!;
    const route = routeFor(state, f.id);
    const len = routeLength(route);

    if (st.finished) {
      const due = state.respawnAt[f.id];
      if (due !== undefined && state.simTimeSec >= due) {
        const fresh = initialTrainStates(state.scenario)[f.id]!;
        state.trains[f.id] = { ...fresh, s: 0, delaySec: fresh.delaySec };
        delete state.respawnAt[f.id];
      }
      continue;
    }

    if (st.holdRemainingSec > 0) {
      st.holdRemainingSec = Math.max(0, st.holdRemainingSec - dt);
      st.delaySec += dt;
      st.state = "HELD";
      continue;
    }
    if (st.dwellRemainingSec > 0) {
      st.dwellRemainingSec = Math.max(0, st.dwellRemainingSec - dt);
      st.state = "DWELL";
      continue;
    }

    const v = kmhToUnitsPerSec(st.speedKmh);
    let remaining = v * dt;
    while (remaining > 0 && !st.finished) {
      const nextStop = route.stops[st.nextStopIndex];
      const target = nextStop ? nextStop.s : len;
      const step = Math.min(remaining, target - st.s);
      st.s += step;
      remaining -= step;
      if (st.s >= target - 0.001) {
        if (nextStop) {
          st.s = nextStop.s;
          st.dwellRemainingSec = nextStop.dwellSec;
          st.nextStopIndex += 1;
          st.state = "DWELL";
          break;
        }
        st.finished = true;
        st.state = "CLEARED";
        state.respawnAt[f.id] = state.simTimeSec + RESPAWN_GAP_SEC;
        break;
      }
    }

    if (!st.finished && st.dwellRemainingSec === 0) {
      st.state = st.speedKmh < st.nominalSpeedKmh - 0.5 ? "REGULATED" : "RUNNING";
      if (st.speedKmh < st.nominalSpeedKmh) {
        st.delaySec += dt * (1 - st.speedKmh / st.nominalSpeedKmh);
      }
      const dToStation = Math.abs(st.s - (route.stops[st.nextStopIndex]?.s ?? -1e9));
      if (dToStation < 120) st.state = "APPROACHING";
    }
  }
  return state;
}

export function advanceTo(state: SimState, targetSimTime: number, step = 2): SimState {
  let out = state;
  let guard = 0;
  while (out.simTimeSec < targetSimTime - 0.001 && guard < 5000) {
    out = tick(out, Math.min(step, targetSimTime - out.simTimeSec));
    guard++;
  }
  return out;
}

interface ResourceUseWindow {
  trainId: string;
  enter: number;
  exit: number;
  s: number;
}

function occupancySeconds(state: SimState, trainId: string, resourceId: string): number {
  const res = resourceById[resourceId]!;
  const st = state.trains[trainId]!;
  const v = kmhToUnitsPerSec(Math.max(1, st.speedKmh));
  const base = (res.radius * 2) / v;
  if (res.kind === "PLATFORM") {
    const route = routeFor(state, trainId);
    const stop = route.stops.find((x) => x.platformId === resourceId);
    return base + (stop?.dwellSec ?? 0);
  }
  return base;
}

export function predict(state: SimState, horizonSec = DEFAULT_HORIZON_SEC): Prediction {
  const windows: Record<string, ResourceUseWindow[]> = {};
  for (const f of fleet) {
    const st = state.trains[f.id];
    if (!st || st.finished) continue;
    const route = routeFor(state, f.id);
    for (const use of routeResourceUse[route.id] ?? []) {
      if (use.s < st.s) continue;
      const t = projectArrival(state, f.id, use.s);
      if (t === null || t > horizonSec) continue;
      const occ = occupancySeconds(state, f.id, use.resourceId);
      (windows[use.resourceId] ??= []).push({
        trainId: f.id,
        enter: t,
        exit: t + occ,
        s: use.s,
      });
    }
  }

  const conflicts: Conflict[] = [];
  for (const [resourceId, list] of Object.entries(windows)) {
    const res = resourceById[resourceId]!;
    const required = res.headwaySec * state.headwayMultiplier;
    const sorted = [...list].sort((a, b) => a.enter - b.enter);

    if (state.blockedResources.includes(resourceId)) {
      for (const w of sorted) {
        conflicts.push({
          id: `CF-BLK-${resourceId}-${w.trainId}`,
          kind: res.kind === "PLATFORM" ? "PLATFORM_OCCUPATION_OVERLAP" : "ROUTE_CONFLICT",
          severity: "CRITICAL",
          resourceId,
          resourceLabel: res.label,
          at: res.at,
          trainA: w.trainId,
          trainB: "",
          etaSec: w.enter,
          separationSec: 0,
          requiredSeparationSec: required,
        });
      }
      continue;
    }

    for (let i = 1; i < sorted.length; i++) {
      const a = sorted[i - 1]!;
      const b = sorted[i]!;
      const separation = b.enter - a.exit;
      if (separation >= required) continue;
      const severity: ConflictSeverity =
        separation < required * 0.6 ? "CRITICAL" : "WARNING";
      conflicts.push({
        id: `CF-${resourceId}-${a.trainId}-${b.trainId}`,
        kind:
          res.kind === "JUNCTION"
            ? "JUNCTION_CONTENTION"
            : res.kind === "PLATFORM"
              ? "PLATFORM_OCCUPATION_OVERLAP"
              : separation < 0
                ? "BLOCK_OCCUPANCY"
                : "HEADWAY_VIOLATION_RISK",
        severity,
        resourceId,
        resourceLabel: res.label,
        at: res.at,
        trainA: a.trainId,
        trainB: b.trainId,
        etaSec: a.enter,
        separationSec: separation,
        requiredSeparationSec: required,
      });
    }
  }

  conflicts.sort((a, b) => {
    if (a.severity !== b.severity) return a.severity === "CRITICAL" ? -1 : 1;
    return a.etaSec - b.etaSec;
  });

  const paths: Record<string, Point[]> = {};
  for (const f of fleet) {
    const st = state.trains[f.id];
    if (!st || st.finished) continue;
    const route = routeFor(state, f.id);
    const future = advanceTo(cloneState(state), state.simTimeSec + horizonSec, 10);
    const endS = future.trains[f.id]!.finished ? routeLength(route) : future.trains[f.id]!.s;
    paths[f.id] = pathSlice(route.path, st.s, endS);
  }

  return { horizonSec, conflicts, paths };
}

/** Positions of every train at `offsetSec` in the future — used by the scrubber. */
export function projectStateAt(state: SimState, offsetSec: number): SimState {
  if (offsetSec <= 0) return state;
  return advanceTo(cloneState(state), state.simTimeSec + offsetSec, 5);
}

export function applyAction(state: SimState, action: ResolutionAction): SimState {
  const next = cloneState(state);
  const st = next.trains[action.trainId];
  if (!st) return next;
  switch (action.kind) {
    case "SPEED_REGULATION":
      st.speedKmh = Math.max(5, action.speedKmh ?? st.speedKmh);
      st.state = "REGULATED";
      break;
    case "HOLD":
      st.holdRemainingSec += action.holdSec ?? 0;
      st.state = "HELD";
      break;
    case "ALTERNATE_ROUTE": {
      if (!action.routeId) break;
      const newRoute = routeById[action.routeId]!;
      const here = trainPoint(next, action.trainId);
      const remapped = crossingDistance(newRoute.path, here, 400);
      next.routeOverrides[action.trainId] = action.routeId;
      st.s = remapped ?? 0;
      st.nextStopIndex = newRoute.stops.findIndex((x) => x.s > st.s);
      if (st.nextStopIndex < 0) st.nextStopIndex = newRoute.stops.length;
      break;
    }
    case "PLATFORM_REASSIGNMENT":
      // Re-assignment is modelled as a route change onto the alternate face.
      if (action.routeId) return applyAction(next, { ...action, kind: "ALTERNATE_ROUTE" });
      break;
    default:
      break;
  }
  next.appliedActions.push(action);
  return next;
}

function delayProfile(state: SimState): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of fleet) {
    const finish = projectFinish(state, f.id);
    out[f.id] = (state.trains[f.id]?.delaySec ?? 0) + (finish ?? 0);
  }
  return out;
}

function safetyFor(
  action: ResolutionAction,
  after: SimState,
  conflict: Conflict,
  resolved: boolean,
): SafetyValidation {
  const st = after.trains[action.trainId];
  const speedOk =
    action.kind !== "SPEED_REGULATION" ||
    ((action.speedKmh ?? 0) >= 15 && (action.speedKmh ?? 0) <= (st?.nominalSpeedKmh ?? 0));
  const routeOk =
    action.kind !== "ALTERNATE_ROUTE" ||
    (!!action.routeId && !after.unavailableRoutes.includes(action.routeId));
  const res = resourceById[conflict.resourceId]!;
  const checks = [
    {
      id: "SEP",
      label: "Separation over contended resource",
      passed: resolved,
      detail: resolved
        ? `Meets ${Math.round(res.headwaySec * after.headwayMultiplier)} s minimum at ${res.id}`
        : `Below ${Math.round(res.headwaySec * after.headwayMultiplier)} s minimum at ${res.id}`,
    },
    {
      id: "SPD",
      label: "Commanded speed within line limit",
      passed: speedOk,
      detail: speedOk ? "Within permissible section speed" : "Outside permissible band",
    },
    {
      id: "RTE",
      label: "Route availability",
      passed: routeOk,
      detail: routeOk ? "Route set is available" : "Route not available in current state",
    },
    {
      id: "PLT",
      label: "Platform / block availability",
      passed: !after.blockedResources.includes(conflict.resourceId),
      detail: after.blockedResources.includes(conflict.resourceId)
        ? "Resource withdrawn from service"
        : "Resource in service",
    },
  ];
  return { passed: checks.every((c) => c.passed), checks };
}

function outcomeFor(
  baseState: SimState,
  baseDelays: Record<string, number>,
  conflict: Conflict,
  horizonSec: number,
  id: string,
  letter: string,
  title: string,
  action: ResolutionAction,
  infrastructureChange: OptionOutcome["infrastructureChange"],
  infeasibleReason?: string,
): OptionOutcome {
  if (infeasibleReason) {
    return {
      id,
      letter,
      title,
      action,
      conflictResolved: false,
      addedDelaySec: {},
      networkDelaySec: 0,
      passengerDelaySec: 0,
      freightDelaySec: 0,
      throughputDelta: 0,
      infrastructureChange,
      safety: { passed: false, checks: [] },
      residualConflicts: 0,
      feasible: false,
      infeasibleReason,
    };
  }
  const after = applyAction(baseState, action);
  const prediction = predict(after, horizonSec);
  const stillThere = prediction.conflicts.find(
    (c) => c.resourceId === conflict.resourceId && c.severity === "CRITICAL",
  );
  const resolved = !stillThere;
  const afterDelays = delayProfile(after);
  const addedDelaySec: Record<string, number> = {};
  let network = 0;
  let passenger = 0;
  let freight = 0;
  for (const f of fleet) {
    const d = (afterDelays[f.id] ?? 0) - (baseDelays[f.id] ?? 0);
    if (Math.abs(d) < 1) continue;
    addedDelaySec[f.id] = d;
    network += d;
    if (f.type === "FREIGHT") freight += d;
    else passenger += d;
  }
  const baseThroughput = throughputWithin(baseState, horizonSec);
  const afterThroughput = throughputWithin(after, horizonSec);
  return {
    id,
    letter,
    title,
    action,
    conflictResolved: resolved,
    addedDelaySec,
    networkDelaySec: network,
    passengerDelaySec: passenger,
    freightDelaySec: freight,
    throughputDelta: afterThroughput - baseThroughput,
    infrastructureChange,
    safety: safetyFor(action, after, conflict, resolved),
    residualConflicts: prediction.conflicts.filter((c) => c.severity === "CRITICAL").length,
    feasible: true,
  };
}

function throughputWithin(state: SimState, horizonSec: number): number {
  let count = 0;
  for (const f of fleet) {
    const t = projectFinish(state, f.id);
    if (t !== null && t <= horizonSec) count++;
  }
  return count;
}

/** Required extra separation to clear the conflict, seconds. */
function neededGap(conflict: Conflict): number {
  return Math.ceil(conflict.requiredSeparationSec - conflict.separationSec) + 20;
}

export function generateOptions(
  state: SimState,
  conflict: Conflict,
  horizonSec = DEFAULT_HORIZON_SEC,
): OptionOutcome[] {
  const baseDelays = delayProfile(state);
  const options: OptionOutcome[] = [];

  const a = conflict.trainA ? fleetById[conflict.trainA] : undefined;
  const b = conflict.trainB ? fleetById[conflict.trainB] : undefined;
  // The train that gives way is the lower-priority movement (higher number).
  const giveWay = a && b ? (a.priority >= b.priority ? a : b) : (a ?? b);
  const keep = a && b ? (giveWay === a ? b : a) : undefined;
  if (!giveWay) return options;

  const gap = neededGap(conflict);
  const occB = conflict.trainB
    ? occupancySeconds(state, conflict.trainB, conflict.resourceId)
    : 0;
  const occA = conflict.trainA
    ? occupancySeconds(state, conflict.trainA, conflict.resourceId)
    : 0;
  // Holding the *earlier* movement means it must fall in behind the other one.
  const holdEarlier = Math.ceil(
    conflict.etaSec +
      occA +
      conflict.separationSec +
      occB +
      conflict.requiredSeparationSec -
      conflict.etaSec,
  );
  const stGive = state.trains[giveWay.id]!;
  const use = (routeResourceUse[routeFor(state, giveWay.id).id] ?? []).find(
    (u) => u.resourceId === conflict.resourceId,
  );
  const distance = use ? use.s - stGive.s : 0;
  const currentEta = projectArrival(state, giveWay.id, use?.s ?? stGive.s) ?? 0;

  let letter = 0;
  const nextLetter = () => String.fromCharCode(65 + letter++);

  // A. Speed regulation on the give-way movement.
  if (distance > 40 && currentEta > 0) {
    const targetEta = currentEta + gap;
    // distance is in map units (10 m each): units -> km/h.
    const speed = Math.round(
      Math.max(10, Math.min(stGive.nominalSpeedKmh, (distance * 10 * 3.6) / targetEta)),
    );
    if (speed >= 15) {
      options.push(
        outcomeFor(
          state,
          baseDelays,
          conflict,
          horizonSec,
          "OPT-SPEED",
          nextLetter(),
          `Regulate ${giveWay.id} to ${speed} km/h`,
          { kind: "SPEED_REGULATION", trainId: giveWay.id, speedKmh: speed },
          "NONE",
        ),
      );
    } else {
      options.push(
        outcomeFor(
          state,
          baseDelays,
          conflict,
          horizonSec,
          "OPT-SPEED",
          nextLetter(),
          `Regulate ${giveWay.id}`,
          { kind: "SPEED_REGULATION", trainId: giveWay.id, speedKmh: speed },
          "NONE",
          "Required speed is below the permissible minimum for this section",
        ),
      );
    }
  }

  // B. Hold the give-way movement short of the resource.
  options.push(
    outcomeFor(
      state,
      baseDelays,
      conflict,
      horizonSec,
      "OPT-HOLD-GIVE",
      nextLetter(),
      `Hold ${giveWay.id} for ${Math.round(gap)} s`,
      { kind: "HOLD", trainId: giveWay.id, holdSec: gap },
      "NONE",
    ),
  );

  // C. Hold the priority movement instead — feasible, but usually worse.
  if (keep) {
    const holdKeep = keep.id === conflict.trainA ? holdEarlier : Math.round(gap);
    options.push(
      outcomeFor(
        state,
        baseDelays,
        conflict,
        horizonSec,
        "OPT-HOLD-KEEP",
        nextLetter(),
        `Hold ${keep.id} for ${holdKeep} s`,
        { kind: "HOLD", trainId: keep.id, holdSec: holdKeep },
        "NONE",
      ),
    );
  }

  // D. Alternate route, where one physically exists for that train.
  const alt = giveWay.alternateRouteIds.find((r) => !state.unavailableRoutes.includes(r));
  if (giveWay.alternateRouteIds.length > 0) {
    const here = trainPoint(state, giveWay.id);
    const remap = alt ? crossingDistance(routeById[alt]!.path, here, 30) : null;
    if (alt && remap !== null) {
      options.push(
        outcomeFor(
          state,
          baseDelays,
          conflict,
          horizonSec,
          "OPT-ROUTE",
          nextLetter(),
          `Route ${giveWay.id} via ${routeById[alt]!.label}`,
          { kind: "ALTERNATE_ROUTE", trainId: giveWay.id, routeId: alt },
          "LOW",
        ),
      );
    } else {
      options.push(
        outcomeFor(
          state,
          baseDelays,
          conflict,
          horizonSec,
          "OPT-ROUTE",
          nextLetter(),
          `Route ${giveWay.id} via goods loop`,
          { kind: "ALTERNATE_ROUTE", trainId: giveWay.id },
          "LOW",
          alt
            ? "Goods loop turnout is already behind this movement"
            : "Goods loop is occupied in the current state",
        ),
      );
    }
  }

  // E. Platform re-assignment — only offered when the contended resource is a
  // platform and the train has a physically compatible alternate face.
  if (resourceById[conflict.resourceId]?.kind === "PLATFORM") {
    const target = giveWay.alternatePlatformIds[0];
    options.push(
      outcomeFor(
        state,
        baseDelays,
        conflict,
        horizonSec,
        "OPT-PLATFORM",
        nextLetter(),
        target ? `Re-platform ${giveWay.id} to ${target}` : `Re-platform ${giveWay.id}`,
        {
          kind: "PLATFORM_REASSIGNMENT",
          trainId: giveWay.id,
          ...(target ? { platformId: target } : {}),
        },
        "MEDIUM",
        target
          ? undefined
          : "No alternate face serves this train's direction of travel at Vasai Road",
      ),
    );
  }

  return options;
}

export function recommend(
  conflict: Conflict,
  options: OptionOutcome[],
): Recommendation | null {
  const viable = options.filter((o) => o.feasible && o.conflictResolved && o.safety.passed);
  if (viable.length === 0) return null;
  // Passenger delay is weighted three times freight delay: passenger impact
  // dominates network cost at a suburban junction.
  const score = (o: OptionOutcome) =>
    o.passengerDelaySec * 3 + o.freightDelaySec + (o.infrastructureChange === "NONE" ? 0 : 60);
  const best = [...viable].sort((x, y) => score(x) - score(y))[0]!;
  const others = viable.filter((o) => o.id !== best.id);
  const keepTrain = conflict.trainA === best.action.trainId ? conflict.trainB : conflict.trainA;
  const keep = fleetById[keepTrain];
  const give = fleetById[best.action.trainId];
  return {
    conflictId: conflict.id,
    optionId: best.id,
    rationale:
      keep && give
        ? `${keep.id} (${keep.type.toLowerCase()}, priority ${keep.priority}) carries the higher passenger impact and is closer to ${conflict.resourceLabel}. ${give.id} (${give.type.toLowerCase()}, priority ${give.priority}) can give way at the lowest network cost.`
        : `Applies the lowest-cost feasible regulation for ${conflict.resourceLabel}.`,
    expectedOutcome: `${conflict.kind.replace(/_/g, " ").toLowerCase()} cleared; separation restored to at least ${Math.round(conflict.requiredSeparationSec)} s over ${conflict.resourceId}.`,
    alternatives: others.map(
      (o) => `${o.title} — network ${(o.networkDelaySec / 60).toFixed(1)} min`,
    ),
  };
}

export function computeKPIs(state: SimState, prediction: Prediction): KPISet {
  const active = fleet.filter((f) => !state.trains[f.id]?.finished);
  const totalDelay = active.reduce((sum, f) => sum + (state.trains[f.id]?.delaySec ?? 0), 0);
  const passengerDelay = active
    .filter((f) => f.type !== "FREIGHT" && f.type !== "SHUNT")
    .reduce((sum, f) => sum + (state.trains[f.id]?.delaySec ?? 0), 0);
  const freightDelay = active
    .filter((f) => f.type === "FREIGHT")
    .reduce((sum, f) => sum + (state.trains[f.id]?.delaySec ?? 0), 0);
  const onTime = active.filter((f) => (state.trains[f.id]?.delaySec ?? 0) <= 180).length;
  const occupied = occupiedPlatforms(state);
  const clearedWithinHour = active.filter((f) => {
    const t = projectFinish(state, f.id);
    return t !== null && t <= 3600;
  }).length;

  return {
    activeConflicts: prediction.conflicts.filter((c) => c.severity === "CRITICAL").length,
    trainsTracked: active.length,
    totalDelaySec: totalDelay,
    averageDelaySec: active.length ? totalDelay / active.length : 0,
    passengerDelaySec: passengerDelay,
    freightDelaySec: freightDelay,
    throughputPerHour: clearedWithinHour > 0 ? clearedWithinHour : null,
    platformUtilisation: Object.keys(occupied).length / 7,
    onTimePercent: active.length ? (onTime / active.length) * 100 : 0,
    recoveryTimeSec: recoveryTime(state, prediction),
  };
}

function recoveryTime(state: SimState, prediction: Prediction): number | null {
  if (prediction.conflicts.length === 0) return null;
  const worst = prediction.conflicts.reduce(
    (max, c) => Math.max(max, c.requiredSeparationSec - c.separationSec),
    0,
  );
  return Math.round(worst + (state.headwayMultiplier - 1) * 120);
}

/** Platform id -> occupying train id, for trains standing at or entering a face. */
export function occupiedPlatforms(state: SimState): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of fleet) {
    const st = state.trains[f.id];
    if (!st || st.finished) continue;
    const route = routeFor(state, f.id);
    for (const stop of route.stops) {
      if (Math.abs(st.s - stop.s) < 40) out[stop.platformId] = f.id;
    }
  }
  return out;
}

/** Next occupancy per platform within the horizon, for the occupancy strip. */
export function platformSchedule(
  state: SimState,
  horizonSec = DEFAULT_HORIZON_SEC,
): Record<string, { trainId: string; etaSec: number }[]> {
  const out: Record<string, { trainId: string; etaSec: number }[]> = {};
  for (const f of fleet) {
    const st = state.trains[f.id];
    if (!st || st.finished) continue;
    const route = routeFor(state, f.id);
    for (const stop of route.stops) {
      if (stop.s < st.s) continue;
      const t = projectArrival(state, f.id, stop.s);
      if (t === null || t > horizonSec) continue;
      (out[stop.platformId] ??= []).push({ trainId: f.id, etaSec: t });
    }
  }
  for (const list of Object.values(out)) list.sort((x, y) => x.etaSec - y.etaSec);
  return out;
}
