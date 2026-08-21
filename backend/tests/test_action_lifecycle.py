"""Controller commands must never leak infrastructure or stack silently."""
from __future__ import annotations

from app.twin.engine import SimulationEngine
from app.twin.state import AppliedAction
from app.network.routes import alternate_platforms
from app.network.net import resources as network_resources


def _advance_until(engine, predicate, limit: int = 1800):
    for _ in range(limit):
        engine.advance(1.0)
        match = next((rt for rt in engine.trains.values() if predicate(rt)), None)
        if match is not None:
            return match
    raise AssertionError("fixture never reached the required operating phase")


def test_interrupting_an_owner_releases_and_closes_occupancy():
    eng = SimulationEngine("BASE", seed=42, stochastic=False)
    rt = _advance_until(eng, lambda item: item.current_resource_id is not None)
    rid = rt.current_resource_id
    resource = eng.resources[rid]
    eng._interrupt(rt.train_id)
    eng.advance(0.1)
    assert all(rec.exit is not None for rec in resource.occupancy)
    assert len(resource.res.users) <= resource.res.capacity
    eng.advance(300.0)
    assert len(resource.res.users) <= resource.res.capacity


def test_interrupting_a_queued_request_cancels_it():
    eng = SimulationEngine("PEAK_SURGE", seed=42, stochastic=False)
    rt = _advance_until(eng, lambda item: item.queued_resource_id is not None)
    rid = rt.queued_resource_id
    resource = eng.resources[rid]
    eng._interrupt(rt.train_id)
    eng.advance(0.1)
    queued_processes = {
        item.train_id for item in eng.trains.values()
        if item.queued_resource_id == rid
    }
    assert len(resource.res.queue) == len(queued_processes)


def test_action_id_is_idempotent_and_holds_do_not_add():
    eng = SimulationEngine("BASE", seed=42, stochastic=False)
    rt = next(iter(eng.trains.values()))
    action = AppliedAction(
        "HOLD", rt.train_id, hold_sec=60, action_id="A-SAME")
    assert eng.apply_action(action)
    first = rt.pending_hold_sec
    assert not eng.apply_action(action)
    assert rt.pending_hold_sec == first

    # A different recommendation for the same release window cannot add
    # another sixty seconds; it only maintains the absolute release target.
    second = AppliedAction(
        "HOLD", rt.train_id, hold_sec=60, action_id="A-REPLAN")
    assert eng.apply_action(second)
    assert rt.pending_hold_sec == first


def test_speed_regulation_expires_by_time():
    eng = SimulationEngine("BASE", seed=42, stochastic=False)
    rt = next(iter(eng.trains.values()))
    assert eng.apply_action(AppliedAction(
        "SPEED_REGULATION", rt.train_id, speed_kmh=20,
        expires_at_sec=eng.now + 2, action_id="A-SPEED"))
    eng.advance(3)
    eng._limit_ms(rt, rt.s)
    assert rt.regulated_kmh is None


def test_pre_admission_replatform_refreshes_the_running_process_route():
    eng = SimulationEngine("BASE", seed=42, stochastic=False)
    chosen = next(
        (rt, eng.routes[rt.train_id])
        for rt in eng.trains.values()
        if rt.entry_at_sec > eng.now + 30
        and eng.routes[rt.train_id].platform_id
        and alternate_platforms(eng.routes[rt.train_id])
    )
    rt, original = chosen
    old_face = original.platform_id
    target = alternate_platforms(original)[0]
    eng.resources[old_face].blocked = True
    eng.blocked_resources.add(old_face)
    assert eng.apply_action(AppliedAction(
        "PLATFORM_REASSIGNMENT", rt.train_id, platform_id=target,
        action_id="PRE-ADMISSION-REPLATFORM"))

    eng.advance(rt.entry_at_sec - eng.now + 2400)
    assert any(record.train_id == rt.train_id
               for record in eng.resources[target].occupancy)
    assert not any(record.train_id == rt.train_id
                   for record in eng.resources[old_face].occupancy)


def test_replatform_is_rejected_after_the_approach_turnout():
    eng = SimulationEngine("BASE", seed=42, stochastic=False)
    rt, route = next(
        (runtime, eng.routes[runtime.train_id])
        for runtime in eng.trains.values()
        if eng.routes[runtime.train_id].platform_id
        and alternate_platforms(eng.routes[runtime.train_id])
    )
    stop_s = route.stops[0].s
    control_s = max(
        use.enter_s for use in route.uses
        if network_resources[use.resource_id].kind == "JUNCTION"
        and use.enter_s < stop_s)
    rt.s = control_s
    assert not eng.apply_action(AppliedAction(
        "PLATFORM_REASSIGNMENT", rt.train_id,
        platform_id=alternate_platforms(route)[0], action_id="TOO-LATE"))
