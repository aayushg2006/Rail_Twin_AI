"""Independent SafetyValidator (Phase 5).

Runs AFTER optimization on a candidate's resulting state and is authoritative:
the optimizer can never override an UNSAFE verdict. Emits the exact
SafetyValidation shape (passed + checks[]) the console's SafetyPanel renders.
"""
from __future__ import annotations

from ..network.net import resources as net_resources
from ..twin.predict import AnalyticState, Conflict
from ..twin.state import AppliedAction


def validate(action: AppliedAction, after: AnalyticState, conflict: Conflict,
             resolved: bool, mode: str = "RESOLUTION") -> dict:
    st = after.trains.get(action.train_id)
    res = net_resources[conflict.resource_id]
    required = round(res.headway_sec * after.headway_multiplier)

    containment = mode == "CONTAINMENT"
    speed_ok = (action.kind != "SPEED_REGULATION"
                or ((action.speed_kmh or 0) >= 15
                    and (action.speed_kmh or 0) <= (st.line_speed_kmh if st else 0)))
    # A re-platforming is only valid onto a face that is actually in service.
    route_ok = (action.kind not in ("ALTERNATE_ROUTE", "PLATFORM_REASSIGNMENT")
                or (bool(action.platform_id or action.route_id)
                    and (action.platform_id or "") not in after.blocked_resources
                    and (action.route_id or "") not in after.unavailable_routes))
    # A containment command may protect a movement from an unavailable
    # resource, but it must not claim that the resource has been restored.
    plt_ok = containment or conflict.resource_id not in after.blocked_resources
    protection_ok = containment and action.kind in ("HOLD", "SPEED_REGULATION")

    checks = [
        {"id": "SEP", "label": "Separation over contended resource", "passed": resolved,
         "detail": (f"Meets {required} s minimum at {res.id}" if resolved
                    else (f"Containment protects the movement; {required} s separation is not restored"
                          if containment else f"Below {required} s minimum at {res.id}"))},
        {"id": "SPD", "label": "Commanded speed within line limit", "passed": speed_ok,
         "detail": "Within permissible section speed" if speed_ok else "Outside permissible band"},
        {"id": "RTE", "label": "Route availability", "passed": route_ok,
         "detail": "Route can be set" if route_ok else "Route or platform not available"},
        {"id": "PLT", "label": "Platform / block availability", "passed": plt_ok,
         "detail": ("Movement protected before the unavailable resource"
                    if containment and conflict.resource_id in after.blocked_resources
                    else "Resource in service" if plt_ok else "Resource withdrawn from service")},
    ]
    if containment:
        checks.append({
            "id": "PROTECT",
            "label": "Protective containment command",
            "passed": protection_ok,
            "detail": ("Hold/regulation prevents unsafe entry while the conflict remains under protection"
                        if protection_ok else "Containment requires a hold or safe speed regulation"),
        })
    passed = (all(c["passed"] for c in checks if c["id"] != "SEP")
              if containment else all(c["passed"] for c in checks))
    return {"passed": passed, "checks": checks, "mode": mode}
