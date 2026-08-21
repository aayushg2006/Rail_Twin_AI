"""Queue rollout parity with small deterministic SimPy schedules."""
from __future__ import annotations

import simpy

from app.twin.predict import (AnalyticResource, AnalyticState, PassWindow,
                              queue_rollout)


def _simpy_fifo(request_times: dict[str, float], duration: float,
                headway: float) -> dict[str, tuple[float, float]]:
    env = simpy.Environment()
    resource = simpy.Resource(env, capacity=1)
    gate = {"free": 0.0}
    result: dict[str, tuple[float, float]] = {}

    def movement(train_id: str, ready: float):
        yield env.timeout(ready)
        with resource.request() as request:
            yield request
            if gate["free"] > env.now:
                yield env.timeout(gate["free"] - env.now)
            enter = env.now
            yield env.timeout(duration)
            result[train_id] = (enter, env.now)
            gate["free"] = env.now + headway

    for train_id, ready in request_times.items():
        env.process(movement(train_id, ready))
    env.run()
    return result


def test_queue_rollout_matches_deterministic_simpy_to_two_seconds():
    resource_id = "J-N"              # 120-second headway in the network pack
    state = AnalyticState(
        sim_time=0.0, service_epoch_sec=0.0, trains={}, routes={},
        resources={resource_id: AnalyticResource(resource_id)},
    )
    free = {
        "A": [PassWindow("A", resource_id, 0.0, 10.0, 0.0)],
        "B": [PassWindow("B", resource_id, 5.0, 15.0, 0.0)],
        "C": [PassWindow("C", resource_id, 20.0, 30.0, 0.0)],
    }
    expected = _simpy_fifo({"A": 0.0, "B": 5.0, "C": 20.0}, 10.0, 120.0)
    rollout = queue_rollout(state, free)
    for train_id, windows in rollout.plans.items():
        assert len(windows) == 1
        assert windows[0].enter == pytest.approx(expected[train_id][0], abs=2.0)
        assert windows[0].exit == pytest.approx(expected[train_id][1], abs=2.0)


# Imported last so the fixture body above stays readable as a SimPy comparison.
import pytest

