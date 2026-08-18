# Vasai Road Digital Twin — UI/UX rebuild against the reference workstation

The station infrastructure stays exactly as built: `src/twin/topology.ts` (PF1–PF7, FOBs, amenities, Diva branch, goods chord, junctions, signals, corridors) is not touched, and the SVG geometry inside the map keeps its current layout. Everything around it — screens, panel system, visual tokens, interaction workflow — is rebuilt to match the reference operations workstation and the new master prompt.

## What changes vs. what is kept

Kept as-is:
- Station topology and geometry data.
- The simulation engine, scenario roster and conflict/option logic in `src/twin/` (extended, not replaced).
- Map drawing of tracks, platforms, signals, amenities, corridors.

Changed:
- 3 screens → 5 screens following the reference workflow.
- Panels rebuilt on a compact primitives kit (`Panel`, `PanelHead`, `Metric`, `Tag`, `Btn`, `Row`).
- Design tokens replaced with the reference's operational palette.
- New behaviours: conflict focus mode, contextual inspector, scenario comparison, timeline ticks NOW/T+2/5/7/10/15.

## Screens (5)

```text
/            LIVE        map (60–70% of screen) + timeline + conflict list + train list + log tail
/conflict    FOCUS       conflict-focused map, de-emphasised network, A vs B trajectories, time-to-conflict
/decision    WHAT-IF     scenario options compared side by side + recommendation + ACCEPT / MODIFY / REJECT
/scenario    SCENARIO    disruption injection + full train roster
/log         RECORD      decision log + compact performance charts (baseline vs optimised)
```

`/analysis` and `/decisions` are removed; their content moves into `/log` and `/decision`. Each route gets its own `head()` metadata.

## Visual system

Adopt the reference's tokens in `src/styles.css`:
- Surfaces: `shell`, `map`, `map-grid`, `panel`, `raised`; lines `line`, `line-strong`.
- Type: `ink`, `ink-dim`, `ink-faint`.
- State-only colour: normal (muted teal-grey), warning amber, conflict muted red, ok green, selected restrained blue, freight olive.
- Infrastructure: `track`, `track-slow`, `track-goods`.
- Radius 0.25rem, no shadows, no gradients. IBM Plex Sans / Mono / Sans Condensed retained.
- Utilities: `label-xs` (mono uppercase micro-label), `num` (tabular mono), `panel-surface`, `hairline-t`, `conflict-pulse`.

New `src/components/ops/primitives.tsx` provides the shared kit; every panel is rewritten on top of it so density and alignment are uniform.

## Behaviour added to the twin

1. **Conflict focus mode** — selecting a conflict dims unrelated trains/tracks, keeps Train A, Train B, the contended resource and both projected trajectories at full contrast, and shows conflict ID, trains, resource, predicted time, time-to-conflict, severity and status inline on the map, no modal.
2. **Contextual inspector** — one panel that changes shape based on the current selection: train (ID, type, block, speed, direction, ETA, delay, priority, projected conflict), platform (state, current train, arrival/departure, next train, utilisation), track/block (state, train, speed, occupation, expected release), conflict (trains, resource, time, prediction, options, recommendation). Replaces the fixed stack of always-on cards.
3. **Timeline** — persistent strip with NOW · T+2 · T+5 · T+7 · T+10 · T+15 ticks plus scrub, play/pause, 1×/2×/5×/10× and NEXT EVENT. Scrubbing updates train positions, predicted routes, platform occupancy and the conflict set together.
4. **Actual / predicted / recommended separation** — solid for actual, dashed segmented for predicted, distinct analytical treatment for the recommended future path, restrained hatch for the conflict zone.
5. **What-if comparison** — options rendered as alternative operational futures in a comparison table (delay, conflicts, throughput, platform/route impact, feasibility, safety validation, recovery), not as feature cards.
6. **Recommendation** — RECOMMENDED ACTION / WHY / EXPECTED EFFECT (network delay vs baseline) / CONFLICT / SAFETY, with SIMULATE · COMPARE · INSPECT · ACCEPT · REJECT · MODIFY. No auto-control affordance.

## Metrics

The KPI bar stays at the bottom of the console, compressed to a single hairline row of `Metric` values (throughput, total/avg delay, passenger delay, freight delay, active conflicts, platform utilisation, on-time %, recovery). Compact analytical charts live only on `/log`. No KPI-card grid anywhere.

## Technical notes

- New: `src/components/ops/primitives.tsx`, `TopBar.tsx`, `Timeline.tsx`, `Inspector.tsx`, `ConflictPanel.tsx`, `OptionsPanel.tsx`, `DecisionPanel.tsx`, `SafetyValidationList.tsx`, `TrainList.tsx`, `KpiBar.tsx`, `DecisionLog.tsx`.
- `src/components/twin/ConsolePanels.tsx` is decomposed into those files and removed.
- `TwinMap` gains `focusConflictId`, `dim`, and `compact` props; its topology rendering is unchanged.
- `src/twin/store.tsx` gains: `selection` (train | platform | track | conflict | null), `focusConflictId`, `decisionStatus`, `previewTime`/`scrubOffset` tick presets, and `modify(optionId, capKmh)`.
- Engine additions only: per-selection inspector projections, platform next-train/utilisation, block occupation and expected release, safety-validation checks per option. No change to topology or route geometry.
- Routes: `src/routes/index.tsx` rewritten; `conflict.tsx`, `decision.tsx`, `scenario.tsx`, `log.tsx` added; `analysis.tsx`, `decisions.tsx` deleted; nav in `__root.tsx` updated to the 5 screens.

## Build order

1. Tokens + primitives kit.
2. Shell: top bar, 5-route nav, bottom KPI bar.
3. Timeline with tick presets and scrub wiring.
4. Map layer updates: actual/predicted/recommended treatments, focus/dim mode.
5. Contextual inspector + conflict panel.
6. What-if comparison + recommendation + decision actions.
7. Scenario and log/performance screens.
8. Full walkthrough pass: select train → predict → focus conflict → simulate → compare → recommend → decide → twin updates.
