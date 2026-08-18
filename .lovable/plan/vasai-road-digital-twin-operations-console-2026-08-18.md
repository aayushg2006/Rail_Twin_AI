# Vasai Road Digital Twin — Operations Console

A railway traffic decision-support frontend for Vasai Road Junction: observe → predict → simulate → recommend → human decision → re-evaluate. Frontend-only, driven by a deterministic in-browser simulation behind a swappable, WebSocket-ready data layer.

## Screens

1. `/` — **Live Operations Console** (primary)
2. `/analysis` — Baseline vs Optimized comparison, KPI trends, scenario outcomes
3. `/log` — Decision log / audit trail

Shared shell: top system bar (product name, Vasai Road Junction, operational status, LIVE vs SIMULATION mode, simulation clock, last update, connection status) and left rail navigation. All three read the same simulation store, so the console never contradicts the analysis page.

## The map (heart of the product)

Custom SVG operational schematic, faithful to the uploaded station diagram:

- Platforms PF1–PF7 in their real relative arrangement, with the West/East side split, PATH on the west, and the six through track pairs.
- Slow/Fast designation and direction arrows per platform line exactly as in the diagram. PF1–PF5 serve the Western line (Churchgate–Virar/Dahanu slow and fast pairs). PF6/PF7 are the eastern island serving long-distance expresses and the Vasai Road–Diva branch (MEMU/passenger services toward Juchandra, Kaman Road, Kharbao, Bhiwandi Road, Kopar, Diva, and onward to Kalyan and Panvel).
- Four foot-over-bridges as horizontal red spans, with lift/escalator markers at their real positions.
- Amenity markers: TICKET (x3), RESERVATION, POLICE, WATER (x4), TOILET (x2) at their diagram positions.
- Direction headers: TOWARD DAHANU ROAD (north), TOWARD CHURCHGATE (south), WEST/EAST, compass rose.
- Schematic approach corridors leaving the station frame, making Vasai Road read as a genuine four-way junction:
  - **North (Western line):** Nalla Sopara → Virar → Dahanu Road / Surat.
  - **South (Western line):** Naigaon → Bhayandar → Borivali → Andheri → Dadar → Churchgate.
  - **East (Vasai Road–Diva branch):** the branch diverges from the PF6/PF7 eastern lines south of the station and swings east — Juchandra → Kaman Road → Kharbao → Bhiwandi Road → Kopar → Diva, with the onward split at Diva toward Kalyan and toward Panvel shown as labelled corridor ends. This is the line that carries expresses avoiding Mumbai plus the Vasai–Diva/Panvel MEMU services.
  - **Freight chord:** the goods/through-freight path from the north corridor onto the Diva branch, bypassing the passenger platforms, with the freight yard on the Diva side. North yard/sidings sit on the Virar side.
- The east-side diverging junction (branch turnout onto the Diva line) is a named, modelled junction resource — it is the scarce infrastructure most conflicts contend for, which is what makes the junction behaviour meaningful.
- Junctions J1–J4, block boundaries, and signal symbols drawn as railway symbols (no Lucide, no map pins).

Every track segment has a direction. Trains are placed on a parametric path along their own track, never floating. No two trains ever run opposite directions on one physical track.

Toggleable layers: Infrastructure · Live state · Predicted state · Decision state.

## Simulation engine

A deterministic tick engine (single authoritative clock) that owns train position, speed, delay, ETA, block/platform occupancy, route locks, conflicts, and KPIs. One clock drives the map, timeline, table, and every timestamp.

- Speed control: 1× / 2× / 5× / 10×, pause, step, and NEXT EVENT — mode always shown explicitly.
- Timeline scrubber for NOW · T+2 · T+5 · T+10 (up to a 15-minute prediction horizon). Scrubbing forward moves trains, occupancy, route state, and surfaces the predicted conflict; map and timeline stay in sync.
- Prediction pass projects each train's trajectory and detects resource contention: route conflict, junction contention, platform occupation overlap, block occupancy, headway violation, yard/mainline conflict, downstream congestion. Never labelled "collision".

**Primary demo scenario (labelled DEMO SCENARIO):** Freight F-4271 running south from the Virar side and routed via the freight chord onto the Diva branch, while Express E-12928 approaches from the Naigaon side for a PF-6 halt before continuing east onto the same Diva branch. Both need the east-side diverging junction and the branch's single-line section within the same window — a junction contention predicted roughly seven minutes out. No head-on movement, no shared physical space: purely competing use of the branch turnout.

Injectable disruptions: freight delay, express delay, platform unavailable, track blockage, signal failure, train breakdown, yard congestion, peak traffic, multiple simultaneous delays. Each triggers a visible recompute of current → future → conflicts → options → recommendation.

## Decision workflow

Conflict panel: type, trains involved, contended resource, time-to-conflict, severity — with the conflict spatially marked on the map (restrained hazard hatching, no neon, no banner).

Options are generated only when operationally feasible for the current state: speed regulation, hold freight, hold express, alternate route, platform reassignment. Each option is actually re-simulated; the comparison table shows conflict outcome, per-train delay, network delay, throughput, infrastructure change, and safety validation — all values produced by the engine, never invented.

Recommendation card answers WHAT / WHY / IMPACT / ALTERNATIVES, with explicit "AI recommends · Human decides" framing. ACCEPT applies the action to simulation state (trajectories, conflict state, occupancy, KPIs all visibly change and the conflict clears), MODIFY opens parameter adjustment and re-simulates, REJECT dismisses with a reason. Every outcome is appended to the decision log.

## Supporting panels

- Train table: TRAIN · TYPE · DIRECTION · LOCATION · SPEED · ETA · DELAY · PRIORITY · STATE. Selecting a train highlights it, its route, its projected path, and its conflicts on the map.
- Operational strip: active conflicts, critical trains, platform occupancy PF1–PF7, junction state, route state, prediction horizon.
- KPI bar: throughput, total/average delay, passenger delay, freight delay, active conflicts, platform utilization, on-time percentage, recovery time. Unavailable metrics render `NO DATA`.

## Visual system

Restrained dark operational palette — near-neutral dark shell with tonal separation between shell, map surface, panels, and elevated controls. Semantic color only: neutral normal, amber warning, red critical conflict, green validated, restrained blue for selected/analytical. Thin borders, near-square corners, no shadows, no gradients, no glass, no glow. Technical typeface (IBM Plex Sans + IBM Plex Mono for all numerals and timestamps), not Inter/Geist/Space Grotesk. Custom railway SVG symbols only — no Lucide icons, no emoji. Desktop-first, degrading to a preserved hierarchy on smaller screens with the map still usable.

## Technical notes

- React + TypeScript + Tailwind v4 tokens in `src/styles.css`; TanStack Router routes for the three screens.
- Domain model in `src/domain/`: `Train`, `TrainState`, `Track`, `Block`, `Platform`, `Signal`, `Route`, `Junction`, `Conflict`, `Prediction`, `Scenario`, `SimulationRun`, `Recommendation`, `KPI`, `Decision`.
- `src/twin/` — topology (Vasai geometry as data, not JSX), tick engine, predictor, conflict detector, option simulator, KPI calculator.
- `src/data/` — a `TwinDataSource` interface with a `MockTwinSource` implementation, so a FastAPI/WebSocket source can be dropped in without touching components.
- State split into live / predicted / simulation / recommendation / UI slices; presentation state never mixed with railway state.
- Components: `TrainMarker`, `TrackSegment`, `PlatformShape`, `SignalSymbol`, `RouteOverlay`, `ConflictZone`, `PredictionPath`, `Timeline`, `TrainTable`, `ConflictPanel`, `RecommendationPanel`, `ScenarioControls`, `KPIBar`, `DecisionLog`, `SafetyValidation`, `SimulationControls`.
- Per-route `head()` metadata on all three screens.

## Build order

1. Design tokens, fonts, app shell, routing.
2. Topology data + SVG map with infrastructure layer.
3. Simulation engine + clock + train motion.
4. Predictor, conflict detection, timeline scrubbing.
5. Conflict panel, option simulation, recommendation, ACCEPT/MODIFY/REJECT.
6. Train table, KPI bar, operational strip, scenario injection.
7. Analysis and decision-log pages.
8. Full demo walkthrough pass against the judge story in the brief.
