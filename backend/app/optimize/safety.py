"""Independent SafetyValidator (Phase 5).

Runs AFTER optimization on a candidate's resulting state and is authoritative:
the optimizer can never override an UNSAFE verdict. Emits the exact
SafetyValidation shape (passed + checks[]) the console's SafetyPanel renders.
"""
from __future__ import annotations

from ..network.topology import resource_by_id
from ..twin.predict import AnalyticState, Conflict
from ..twin.state import AppliedAction


def validate(action: AppliedAction, after: AnalyticState, conflict: Conflict,
             resolved: bool) -> dict:
    st = after.trains.get(action.train_id)
    res = resource_by_id[conflict.resource_id]
    required = round(res.headway_sec * after.headway_multiplier)

    speed_ok = (action.kind != "SPEED_REGULATION"
                or ((action.speed_kmh or 0) >= 15
                    and (action.speed_kmh or 0) <= (st.nominal_speed_kmh if st else 0)))
    route_ok = (action.kind != "ALTERNATE_ROUTE"
                or (bool(action.route_id) and action.route_id not in after.unavailable_routes))
    plt_ok = conflict.resource_id not in after.blocked_resources

    checks = [
        {"id": "SEP", "label": "Separation over contended resource", "passed": resolved,
         "detail": (f"Meets {required} s minimum at {res.id}" if resolved
                    else f"Below {required} s minimum at {res.id}")},
        {"id": "SPD", "label": "Commanded speed within line limit", "passed": speed_ok,
         "detail": "Within permissible section speed" if speed_ok else "Outside permissible band"},
        {"id": "RTE", "label": "Route availability", "passed": route_ok,
         "detail": "Route set is available" if route_ok else "Route not available in current state"},
        {"id": "PLT", "label": "Platform / block availability", "passed": plt_ok,
         "detail": "Resource in service" if plt_ok else "Resource withdrawn from service"},
    ]
    return {"passed": all(c["passed"] for c in checks), "checks": checks}
