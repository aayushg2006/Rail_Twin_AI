"""Paired, fixed-cohort release benchmark.

The benchmark is deliberately outside the live orchestrator.  Each manifest
row creates two fresh authoritative SimPy twins with the same epoch, scenario,
seed, passenger anchors, freight paths and keyed dwell samples.  The only
difference is whether the receding-horizon controller may issue a command.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import hmac
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..network.fleet import FleetEntry, fleet, fleet_by_id
from ..network.net import platforms
from ..optimize.engine import OptimizationEngine
from ..twin.engine import SimulationEngine
from ..twin.predict import predict

WARMUP_SEC = 30 * 60
MEASUREMENT_SEC = 60 * 60
DRAIN_SEC = 2 * 60 * 60
STEP_SEC = 60
REPLAN_SEC = 180
MIN_REPLAN_SEC = 60

TARGETS = {
    "totalDelayReductionPercent": 16.2,
    "conflictReductionPercent": 15.6,
    "averageDelayReductionPercent": 18.2,
    "throughputIncreasePercent": 24.6,
    "platformUtilisationIncreasePercent": 26.5,
}

METRIC_DEFINITIONS = {
    "totalDelay": "Mean per-run sum of positive terminal delay for the fixed cohort.",
    "averageDelay": "Pooled positive terminal delay divided by every cohort train; on-time trains contribute zero.",
    "conflicts": "Unique episodes keyed by resource, train pair and occurrence, not prediction ticks.",
    "throughput": "Fixed-cohort movements clearing the section during the measured hour.",
    "platformUtilisation": "Productive occupancy divided by available in-service platform-face seconds; blocked-face time is removed from both paired denominators.",
}


def _data_dir() -> Path:
    candidates = [Path.cwd() / "data", Path(__file__).resolve().parents[3] / "data"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def manifest_path() -> Path:
    return _data_dir() / "benchmark-100.json"


def report_path() -> Path:
    configured = Path(settings.benchmark_report_path)
    if configured.is_absolute():
        return configured
    return _data_dir() / configured.name


@dataclass
class EpisodeTracker:
    active: dict[tuple[str, tuple[str, ...], str], int] = field(default_factory=dict)
    occurrences: dict[tuple[str, tuple[str, ...], str], int] = field(default_factory=dict)
    episodes: set[tuple[str, tuple[str, ...], str, int]] = field(default_factory=set)
    critical: set[tuple[str, tuple[str, ...], str, int]] = field(default_factory=set)

    def observe(self, conflicts) -> None:
        present: set[tuple[str, tuple[str, ...], str]] = set()
        for conflict in conflicts:
            pair = tuple(sorted(t for t in (conflict.train_a, conflict.train_b) if t))
            key = (conflict.resource_id, pair, conflict.kind)
            present.add(key)
            if key not in self.active:
                occurrence = self.occurrences.get(key, 0) + 1
                self.occurrences[key] = occurrence
                self.active[key] = occurrence
            episode = (*key, self.active[key])
            self.episodes.add(episode)
            if conflict.severity == "CRITICAL":
                self.critical.add(episode)
        for key in set(self.active) - present:
            self.active.pop(key, None)


def _records(engine: SimulationEngine) -> dict[str, Any]:
    return {**engine.completed_trains, **engine.trains}


def _cohort(engine: SimulationEngine) -> list[FleetEntry]:
    start = engine.service_epoch_sec + WARMUP_SEC
    end = start + MEASUREMENT_SEC
    return [entry for entry in fleet
            if entry.runs_on(engine.weekday_sun0)
            and start <= entry.entry_sec < end]


def _clear_incident(engine: SimulationEngine, at_sec: float) -> None:
    for resource_id in list(engine.blocked_resources):
        engine.clear_resource(resource_id)
    if engine.headway_multiplier != 1.0:
        engine.set_headway_multiplier(1.0)


def _controller_step(engine: SimulationEngine, optimizer: OptimizationEngine,
                     issued: set[tuple[str, str, str]], last_signature: tuple,
                     last_plan_at: float) -> tuple[tuple, float]:
    state = engine.analytic_state()
    prediction = predict(state)
    # A plan revision is tied to the urgent episode. ETA countdown changes do
    # not cause oscillating commands; a new urgent conflict does.
    urgent = prediction.conflicts[0] if prediction.conflicts else None
    signature = ((urgent.episode_id, urgent.severity),) if urgent else ()
    if engine.now - last_plan_at < MIN_REPLAN_SEC:
        return last_signature, last_plan_at
    if signature == last_signature and engine.now - last_plan_at < REPLAN_SEC:
        return last_signature, last_plan_at
    joint = optimizer.solve_joint(state, prediction, time_limit_sec=0.15)
    first = joint.actions[0] if joint.actions else None
    conflict = next((item for item in prediction.conflicts
                     if item.id == getattr(first, "reason_conflict_id", None)), None)
    if conflict is not None:
        result = optimizer.optimize(state, conflict, joint=joint)
        if (result.recommendation or {}).get("status") == "READY" and result.selected:
            action = result.selected.action
            revision = (action.kind, action.train_id,
                        action.effective_resource_id or conflict.resource_id)
            if revision not in issued and engine.apply_action(action):
                issued.add(revision)    # execute first safe action, then replan
    return signature, engine.now


def _metrics(engine: SimulationEngine, cohort: list[FleetEntry], tracker: EpisodeTracker,
             initially_blocked: set[str], clear_at: float) -> dict[str, Any]:
    records = _records(engine)
    measurement_start = engine.service_epoch_sec + WARMUP_SEC
    measurement_end = measurement_start + MEASUREMENT_SEC
    delays: list[float] = []
    unfinished: list[str] = []
    throughput = 0
    for entry in cohort:
        runtime = records.get(entry.id)
        if runtime is None or runtime.actual_exit_sec is None:
            unfinished.append(entry.id)
            delays.append(float(DRAIN_SEC + MEASUREMENT_SEC))
            continue
        delays.append(max(0.0, runtime.actual_exit_sec - entry.clear_sec))
        if measurement_start <= runtime.actual_exit_sec < measurement_end:
            throughput += 1

    window_start = WARMUP_SEC
    window_end = WARMUP_SEC + MEASUREMENT_SEC
    occupied = 0.0
    for platform_id in platforms:
        managed = engine.resources[platform_id]
        for record in managed.occupancy:
            exit_at = record.exit if record.exit is not None else engine.now
            occupied += max(0.0, min(window_end, exit_at) - max(window_start, record.enter))
    blocked_seconds = 0.0
    for platform_id in initially_blocked & set(platforms):
        blocked_seconds += max(0.0, min(window_end, clear_at) - window_start)
    available = max(1.0, len(platforms) * MEASUREMENT_SEC - blocked_seconds)
    return {
        "cohortTrains": len(cohort),
        "unfinished": unfinished,
        "totalDelaySec": sum(delays),
        "averageDelaySec": sum(delays) / max(1, len(cohort)),
        "conflicts": len(tracker.episodes),
        "criticalConflicts": len(tracker.critical),
        "throughputPerHour": float(throughput),
        "platformUtilisation": occupied / available,
    }


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    args = dict(
        scenario_id=case["scenario"], seed=int(case["seed"]),
        epoch_start_ms=int(case["epochStartMs"]), clock_mode="DEMO",
        stochastic=True, admission_cutoff_sec=WARMUP_SEC + MEASUREMENT_SEC,
    )
    ai = SimulationEngine(**args, accept_actions=True)
    fifo = SimulationEngine(**args, accept_actions=False)
    cohort_ids = {entry.id for entry in _cohort(ai)}
    cohort = [fleet_by_id[train_id] for train_id in sorted(cohort_ids)]
    ai_tracker, fifo_tracker = EpisodeTracker(), EpisodeTracker()
    optimizer = OptimizationEngine()
    issued: set[tuple[str, str, str]] = set()
    signature: tuple = ()
    last_plan_at = -REPLAN_SEC
    clear_at = float(case.get("incidentClearAtSec", WARMUP_SEC + MEASUREMENT_SEC))
    ai_blocked, fifo_blocked = set(ai.blocked_resources), set(fifo.blocked_resources)
    incident_cleared = False
    total = WARMUP_SEC + MEASUREMENT_SEC + DRAIN_SEC
    while ai.now < total:
        step = min(STEP_SEC, total - ai.now)
        ai.advance(step)
        fifo.advance(step)
        if not incident_cleared and ai.now >= clear_at:
            _clear_incident(ai, clear_at)
            _clear_incident(fifo, clear_at)
            incident_cleared = True
        if ai.now <= WARMUP_SEC + MEASUREMENT_SEC:
            signature, last_plan_at = _controller_step(
                ai, optimizer, issued, signature, last_plan_at)
        if WARMUP_SEC <= ai.now <= WARMUP_SEC + MEASUREMENT_SEC:
            ai_tracker.observe(predict(ai.analytic_state()).conflicts)
            fifo_tracker.observe(predict(fifo.analytic_state()).conflicts)

    ai_metrics = _metrics(ai, cohort, ai_tracker, ai_blocked, clear_at)
    fifo_metrics = _metrics(fifo, cohort, fifo_tracker, fifo_blocked, clear_at)
    return {
        "runId": case["runId"], "scenario": case["scenario"],
        "windowClass": case["windowClass"], "seed": case["seed"],
        "epochStartMs": case["epochStartMs"], "cohortIds": sorted(cohort_ids),
        "actionsApplied": len(ai.applied_actions),
        "actionKinds": [action.kind for action in ai.applied_actions],
        "ai": ai_metrics, "fifo": fifo_metrics,
        "delayDeltaSec": ai_metrics["totalDelaySec"] - fifo_metrics["totalDelaySec"],
        "criticalConflictDelta": (ai_metrics["criticalConflicts"]
                                  - fifo_metrics["criticalConflicts"]),
    }


def _percent_reduction(ai: float, fifo: float) -> float:
    return 0.0 if fifo == 0 else (fifo - ai) / fifo * 100.0


def _percent_increase(ai: float, fifo: float) -> float:
    return 0.0 if fifo == 0 else (ai - fifo) / fifo * 100.0


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = max(1, len(rows))
    cohort = sum(row["ai"]["cohortTrains"] for row in rows)
    ai_delay = sum(row["ai"]["totalDelaySec"] for row in rows)
    fifo_delay = sum(row["fifo"]["totalDelaySec"] for row in rows)
    ai_conflicts = sum(row["ai"]["conflicts"] for row in rows)
    fifo_conflicts = sum(row["fifo"]["conflicts"] for row in rows)
    ai_throughput = sum(row["ai"]["throughputPerHour"] for row in rows) / count
    fifo_throughput = sum(row["fifo"]["throughputPerHour"] for row in rows) / count
    ai_util = sum(row["ai"]["platformUtilisation"] for row in rows) / count
    fifo_util = sum(row["fifo"]["platformUtilisation"] for row in rows) / count
    improvements = {
        "totalDelayReductionPercent": _percent_reduction(ai_delay, fifo_delay),
        "conflictReductionPercent": _percent_reduction(ai_conflicts, fifo_conflicts),
        "averageDelayReductionPercent": _percent_reduction(
            ai_delay / max(1, cohort), fifo_delay / max(1, cohort)),
        "throughputIncreasePercent": _percent_increase(ai_throughput, fifo_throughput),
        "platformUtilisationIncreasePercent": _percent_increase(ai_util, fifo_util),
    }
    return {
        "runs": len(rows), "cohortTrains": cohort,
        "ai": {"totalDelayMinutes": ai_delay / count / 60.0,
               "averageDelayMinutes": ai_delay / max(1, cohort) / 60.0,
               "conflicts": ai_conflicts / count, "throughputPerHour": ai_throughput,
               "platformUtilisationPercent": ai_util * 100.0},
        "fifo": {"totalDelayMinutes": fifo_delay / count / 60.0,
                 "averageDelayMinutes": fifo_delay / max(1, cohort) / 60.0,
                 "conflicts": fifo_conflicts / count, "throughputPerHour": fifo_throughput,
                 "platformUtilisationPercent": fifo_util * 100.0},
        "improvements": improvements,
    }


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=3, check=True).stdout.strip()
    except Exception:
        return "UNKNOWN"


def run_benchmark(limit: int | None = None, workers: int = 4) -> dict[str, Any]:
    manifest_bytes = manifest_path().read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = manifest["runs"][:limit]
    if workers > 1 and len(cases) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(cases))) as pool:
            rows = list(pool.map(run_case, cases))
    else:
        rows = [run_case(case) for case in cases]
    aggregate = _aggregate(rows)
    improvements = aggregate["improvements"]
    metric_gates = {name: improvements[name] >= target
                    for name, target in TARGETS.items()}
    reliability = {
        "noRunWorseThanOneSecond": all(row["delayDeltaSec"] <= 1.0 for row in rows),
        "noAdditionalCriticalConflict": all(row["criticalConflictDelta"] <= 0 for row in rows),
        "runsImprovedMoreThanOneSecond": sum(row["delayDeltaSec"] < -1.0 for row in rows),
        "requiredImprovedRuns": 60 if len(rows) == 100 else None,
        "allCohortTrainsCompleted": all(
            not row[side]["unfinished"] for row in rows for side in ("ai", "fifo")),
    }
    reliability_passed = (
        reliability["noRunWorseThanOneSecond"]
        and reliability["noAdditionalCriticalConflict"]
        and reliability["allCohortTrainsCompleted"]
        and (len(rows) == 100 and reliability["runsImprovedMoreThanOneSecond"] >= 60))
    signing_key = settings.benchmark_signing_key
    report = {
        "schemaVersion": 1, "status": "NOT_VALIDATED", "validated": False,
        "metricDefinitions": METRIC_DEFINITIONS, "targets": TARGETS,
        "manifest": manifest_path().name,
        "manifestChecksumSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "codeCommit": _git_commit(), "seeds": [row["seed"] for row in rows],
        "aggregate": aggregate, "metricGates": metric_gates,
        "reliability": reliability, "completeManifest": len(rows) == 100,
        "signatureAlgorithm": "HMAC-SHA256" if signing_key else "UNAVAILABLE",
    }
    passed = len(rows) == 100 and all(metric_gates.values()) and reliability_passed
    # A report cannot call itself validated when its integrity is unverifiable.
    report["validated"] = bool(passed and signing_key)
    report["status"] = "VALIDATED" if report["validated"] else "NOT_VALIDATED"
    payload = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["signature"] = (hmac.new(signing_key.encode(), payload, hashlib.sha256).hexdigest()
                           if signing_key else None)

    output = report_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output.with_name("benchmark-runs.csv")
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["runId", "scenario", "windowClass", "seed", "actionsApplied",
                         "actionKinds",
                         "aiTotalDelaySec", "fifoTotalDelaySec", "delayDeltaSec",
                         "aiConflicts", "fifoConflicts", "aiCritical", "fifoCritical",
                         "aiThroughput", "fifoThroughput", "aiPlatformUtilisation",
                         "fifoPlatformUtilisation", "aiUnfinished", "fifoUnfinished"])
        for row in rows:
            writer.writerow([row["runId"], row["scenario"], row["windowClass"], row["seed"],
                             row["actionsApplied"], "|".join(row["actionKinds"]),
                             row["ai"]["totalDelaySec"],
                             row["fifo"]["totalDelaySec"], row["delayDeltaSec"],
                             row["ai"]["conflicts"], row["fifo"]["conflicts"],
                             row["ai"]["criticalConflicts"], row["fifo"]["criticalConflicts"],
                             row["ai"]["throughputPerHour"], row["fifo"]["throughputPerHour"],
                             row["ai"]["platformUtilisation"], row["fifo"]["platformUtilisation"],
                             len(row["ai"]["unfinished"]), len(row["fifo"]["unfinished"])])
    report["rawCsv"] = raw_path.name
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def load_latest_report() -> dict[str, Any]:
    path = report_path()
    if not path.exists():
        return {
            "schemaVersion": 1, "status": "NOT_VALIDATED", "validated": False,
            "reason": "No completed 100-run benchmark report is available.",
            "targets": TARGETS, "metricDefinitions": METRIC_DEFINITIONS,
        }
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "NOT_VALIDATED", "validated": False,
                "reason": f"Benchmark report is unreadable: {exc}"}
    if not report.get("signature") or report.get("signatureAlgorithm") != "HMAC-SHA256":
        report.update(status="NOT_VALIDATED", validated=False,
                      reason="Benchmark report has no verifiable signature.")
        return report
    key = settings.benchmark_signing_key
    if not key:
        report.update(status="NOT_VALIDATED", validated=False,
                      reason="Benchmark signing key is unavailable.")
        return report
    signature = report.pop("signature")
    raw_csv = report.pop("rawCsv", None)
    expected = hmac.new(
        key.encode(), json.dumps(report, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256).hexdigest()
    report["signature"] = signature
    if raw_csv is not None:
        report["rawCsv"] = raw_csv
    if not hmac.compare_digest(signature, expected):
        report.update(status="NOT_VALIDATED", validated=False,
                      reason="Benchmark report signature does not match its contents.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the paired RAIL-TWIN benchmark")
    parser.add_argument("--limit", type=int, default=None,
                        help="Diagnostic subset; it can never produce VALIDATED status")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of paired cases to execute concurrently")
    args = parser.parse_args()
    report = run_benchmark(args.limit, max(1, args.workers))
    print(json.dumps({"status": report["status"], "aggregate": report["aggregate"],
                      "reliability": report["reliability"]}, indent=2))


if __name__ == "__main__":
    main()
