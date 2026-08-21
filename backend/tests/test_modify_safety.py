"""A controller may modify a recommendation, but never past the interlocking.

Safety is not advisory. A command the interlocking would refuse must be
impossible to issue - the console disables the control and says why, rather than
accepting the click and rejecting it afterwards. These tests pin the rules the
console's Apply button is gated on.
"""
from __future__ import annotations

import pytest

from app.optimize.safety import validate
from app.optimize.provider import build_options
from app.orchestrator.orchestrator import SimulationOrchestrator
from app.twin.predict import predict
from app.twin.state import AppliedAction


@pytest.fixture(scope="module")
def scene():
    orch = SimulationOrchestrator("SIGNAL_DEGRADED")
    orch.engine.advance(300)
    orch.shadow_nothing.advance(300)
    orch.shadow_priority.advance(300)
    orch._refresh_derived()
    orch._refresh_shadows()
    pred = orch._cached_prediction
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    return orch, pred.conflicts[0]


def _check(orch, conflict, action: dict) -> dict:
    orch._validate_proposed({"conflictId": conflict.id, "action": action})
    return orch._last_validation


# ------------------------------------------------------------------- speed
def test_a_speed_above_the_line_limit_is_refused(scene):
    orch, conflict = scene
    limit = orch.engine.trains[conflict.train_a].line_speed_kmh
    verdict = _check(orch, conflict, {
        "kind": "SPEED_REGULATION", "trainId": conflict.train_a,
        "speedKmh": limit + 40})
    assert verdict["passed"] is False
    assert "permissible" in verdict["reason"].lower()


def test_a_crawl_below_the_section_minimum_is_refused(scene):
    orch, conflict = scene
    verdict = _check(orch, conflict, {
        "kind": "SPEED_REGULATION", "trainId": conflict.train_a, "speedKmh": 5})
    assert verdict["passed"] is False


# --------------------------------------------------------------- platforms
def test_a_platform_that_does_not_exist_is_refused(scene):
    """`PF9` was accepted once, because the check only asked whether it was
    blocked - and a platform that does not exist is never blocked."""
    orch, conflict = scene
    verdict = _check(orch, conflict, {
        "kind": "PLATFORM_REASSIGNMENT", "trainId": conflict.train_a,
        "platformId": "PF9"})
    assert verdict["passed"] is False
    assert "not a platform" in verdict["reason"]


def test_a_platform_that_cannot_take_the_movement_is_refused(scene):
    orch, conflict = scene
    route = orch.engine.routes[conflict.train_a]
    from app.network.net import platforms
    from app.network.routes import alternate_platforms
    admissible = set(alternate_platforms(route)) | {route.platform_id}
    wrong = next((p for p in platforms if p not in admissible), None)
    if wrong is None:
        pytest.skip("every face can take this movement")
    verdict = _check(orch, conflict, {
        "kind": "PLATFORM_REASSIGNMENT", "trainId": conflict.train_a,
        "platformId": wrong})
    assert verdict["passed"] is False


def test_a_withdrawn_platform_is_refused(scene):
    orch, conflict = scene
    train_id = next(
        train_id for train_id, route in orch.engine.routes.items()
        if route.platform_id is not None and not orch.engine.trains[train_id].finished)
    route = orch.engine.routes[train_id]
    face = route.platform_id
    assert face is not None
    orch.engine.blocked_resources.add(face)
    try:
        verdict = _check(orch, conflict, {
            "kind": "PLATFORM_REASSIGNMENT", "trainId": train_id,
            "platformId": face})
        assert verdict["passed"] is False
        assert "withdrawn" in verdict["reason"]
    finally:
        orch.engine.blocked_resources.discard(face)


def test_a_command_naming_nothing_is_refused(scene):
    orch, conflict = scene
    verdict = _check(orch, conflict, {
        "kind": "PLATFORM_REASSIGNMENT", "trainId": conflict.train_a})
    assert verdict["passed"] is False


# ------------------------------------------------------ the decision itself
def test_an_unsafe_modification_is_never_applied(scene):
    """The socket path, not just the pre-check: an unsafe command sent anyway
    must be recorded as REJECTED and must not reach the twin."""
    orch, conflict = scene
    limit = orch.engine.trains[conflict.train_a].line_speed_kmh
    before = len(orch.engine.applied_actions)
    orch._decide({
        "conflictId": conflict.id, "outcome": "MODIFIED",
        "action": {"kind": "SPEED_REGULATION", "trainId": conflict.train_a,
                   "speedKmh": limit + 60},
        "note": "deliberately unsafe",
    })
    assert orch.decisions[-1]["outcome"] == "REJECTED"
    assert len(orch.engine.applied_actions) == before, "an unsafe action was applied"


def test_the_reason_names_the_direct_cause_not_a_consequence(scene):
    """A speed breach must read as a speed breach. Separation fails as a knock-on
    of almost every bad command, so it must not be what the controller is told."""
    orch, conflict = scene
    limit = orch.engine.trains[conflict.train_a].line_speed_kmh
    verdict = _check(orch, conflict, {
        "kind": "SPEED_REGULATION", "trainId": conflict.train_a,
        "speedKmh": limit + 40})
    assert "minimum at" not in verdict["reason"], (
        f"reported the separation consequence: {verdict['reason']}")


def test_validation_reports_whether_it_would_clear_the_conflict(scene):
    orch, conflict = scene
    verdict = _check(orch, conflict, {
        "kind": "HOLD", "trainId": conflict.train_a, "holdSec": 5})
    assert "clears" in verdict
    assert isinstance(verdict["clears"], bool)


def test_live_what_if_is_revalidated_applied_and_drives_the_future_twin():
    """The result shown in What-if must be the command the SimPy twin follows.

    This covers the complete backend half of the operator workflow: calculate
    an improving option on the current frame, accept it through the socket
    handler, preserve its projected saving in the record, and keep the changed
    route when simulated time advances.
    """
    orch = SimulationOrchestrator("PLATFORM_BLOCKED")
    for engine in (orch.engine, orch.shadow_nothing, orch.shadow_priority):
        engine.advance(60)
    orch._refresh_derived()
    orch._refresh_shadows()
    result = build_options(orch.engine, orch._cached_prediction)

    choice = None
    conflict = None
    recommendation = None
    for episode in orch._cached_prediction.conflicts:
        rec = result["recommendationByConflict"].get(episode.id) or {}
        option = next(
            (item for item in result["optionsByConflict"].get(episode.id, [])
             if item["id"] == rec.get("optionId")), None)
        if rec.get("status") == "READY" and option is not None:
            conflict, recommendation, choice = episode, rec, option
            break

    assert conflict is not None and choice is not None and recommendation is not None
    assert choice["networkDelaySavingSec"] >= 1.0
    train_id = choice["action"]["trainId"]
    target_platform = choice["action"].get("platformId")
    before_actions = len(orch.engine.applied_actions)

    orch._decide({
        "conflictId": conflict.id,
        "outcome": "ACCEPTED",
        "action": choice["action"],
        "optionTitle": choice["title"],
        "responseMode": recommendation["mode"],
    })

    record = orch.decisions[-1]
    assert record["outcome"] == "ACCEPTED", record.get("reason")
    assert record["projectedDelaySavingSec"] >= 1.0
    assert len(orch.engine.applied_actions) == before_actions + 1
    if target_platform is not None:
        assert orch.engine.routes[train_id].platform_id == target_platform
    orch.engine.advance(30)
    if target_platform is not None:
        assert orch.engine.routes[train_id].platform_id == target_platform
