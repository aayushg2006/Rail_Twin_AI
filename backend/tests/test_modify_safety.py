"""A controller may modify a recommendation, but never past the interlocking.

Safety is not advisory. A command the interlocking would refuse must be
impossible to issue - the console disables the control and says why, rather than
accepting the click and rejecting it afterwards. These tests pin the rules the
console's Apply button is gated on.
"""
from __future__ import annotations

import pytest

from app.optimize.safety import validate
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
    route = orch.engine.routes[conflict.train_a]
    face = route.platform_id
    if face is None:
        pytest.skip("this movement does not use a platform")
    orch.engine.blocked_resources.add(face)
    try:
        verdict = _check(orch, conflict, {
            "kind": "PLATFORM_REASSIGNMENT", "trainId": conflict.train_a,
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
