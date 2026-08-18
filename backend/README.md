# RAIL-TWIN backend — AI-powered predictive train traffic management

The authoritative **brain** behind the Vasai Road operations console. A SimPy
discrete-event digital twin, XGBoost prediction, OR-Tools CP-SAT optimization and
an independent safety validator — streamed to the React console over WebSocket.
Nothing downstream is hard-coded: a scenario is a deterministic *trigger*; every
number the console shows is **computed** by these algorithms.

## The value path (all computed, none scripted)

```
Railway state  →  data ingestion / normalization  →  Vasai Road digital twin
  →  train movement simulation (SimPy)  →  ETA / delay prediction (XGBoost)
  →  delay propagation (causal chain)   →  conflict / bottleneck detection
  →  candidate actions  →  what-if simulation  →  optimization (OR-Tools CP-SAT)
  →  safety validation  →  explainable recommendation  →  human controller
  →  accept / modify / reject  →  updated twin  →  measured before-vs-after
```

## Run

### Local (no Docker)
```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```
Then start the console from the repo root: `npm install && npm run dev`
(defaults to `ws://localhost:8000/ws`). The TopBar shows **Backend twin · live**
when connected; if the backend is down the console falls back to the in-browser
mock automatically.

### Docker (full stack incl. Postgres/PostGIS + Redis)
```bash
docker compose up          # from the repo root
# console: http://localhost:8080   backend: http://localhost:8000
```
Postgres/Redis are optional — the backend runs fully in-memory if they are absent.

## Train / re-evaluate the ML models
```bash
cd backend
python -m app.prediction.train 40     # 40 twin episodes -> artifacts + metrics
```
Prints XGBoost-vs-baseline metrics and writes `app/prediction/artifacts/`
(`eta.ubj`, `delay.ubj`, `conflict.ubj`, `metrics.json`, `registry.json`).

## Tests
```bash
cd backend && python -m pytest -q
```
Covers geometry/topology **parity vs the frontend TS**, train movement, resource
locking, delay accumulation, propagation + causal chain, scenario execution,
reproducibility, conflict detection, candidate generation, what-if isolation,
CP-SAT optimization, hard constraints, safety rejection, accept/modify/reject,
dataset generation, inference determinism and low-confidence fallback.

## Layout
```
app/network/     1:1 port of the frontend network (geometry, topology, fleet, scenarios)
app/twin/        Phase 3 — SimPy twin: engine, resources, state, delay, predict, metrics
app/prediction/  Phase 4 — XGBoost: features, dataset, train, registry, service (+ SHAP)
app/optimize/    Phase 5 — candidates, what-if, objective, CP-SAT engine, safety
app/orchestrator Phase 6 — SimulationOrchestrator (start/pause/inject/tick/…)
app/api/         WebSocket protocol + REST (scenarios, state, metrics, network, audit)
app/domain/      DTO adapters -> exact src/domain/types.ts shapes
app/persistence/ optional Postgres audit log + Redis (graceful)
```

## Metric definitions
- `total_delay` = Σ(current delay over active trains), seconds
- `throughput_per_hour` = trains projected to clear within 3600 s
- `platform_utilisation` = occupied platform faces / 7
- `on_time_percent` = share of active trains with delay ≤ 180 s
- `utilisation` = occupied_time / available_time (per resource)

## Safety note
Decision-support only. The system recommends hold / release / route / priority /
platform changes; it does **not** control signals, replace interlocking or Kavach,
or execute safety-critical commands. Every recommendation passes the independent
`SafetyValidator` (which the optimizer cannot override) before it is presented as
feasible, and the human controller is always the final decision maker.

## WebSocket protocol
- **server → client** `{type:"snapshot", simState, prediction, kpis, options,
  recommendation, optionsByConflict, causalChain, delayBuckets, mlByConflict,
  mlByTrain, baselineKpis, connection}` (~4 Hz)
- **client → server** `{cmd: pause|resume|set_speed|seek|apply_action|decide|
  load_scenario|inject_event|set_horizon|reset, …}`
